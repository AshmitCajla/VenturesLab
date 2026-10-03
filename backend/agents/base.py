"""Shared machinery for all 12 agents.

Each agent is declared as an ``AgentSpec`` (what it reads, what it asks,
what it returns). ``make_node`` turns a spec into a LangGraph node that:

1. skips cleanly if an upstream dependency failed,
2. retrieves the pitch-deck passages relevant to *this* agent (RAG),
3. optionally runs a live web search,
4. calls the LLM with a Pydantic schema (structured output + provider fallback),
5. runs the agent's evaluation checks and, if a hard check fails, retries
   once with the failure fed back to the model (self-correction),
6. records timing, retries and check results in the shared state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from core.model_router import Role, get_structured_llm
from evaluation.checks import run_checks
from services.retrieval import format_context, get_store
from services.web_research import search_market_data

logger = logging.getLogger("agents")

PromptBuilder = Callable[[dict, "AgentContext"], str]
PostProcess = Callable[[BaseModel, dict], dict]
QueryBuilder = Callable[[dict], str]


@dataclass
class AgentContext:
    deck: str = ""
    web: str = ""


@dataclass
class AgentSpec:
    number: int
    name: str
    output_key: str
    schema: type[BaseModel]
    role: Role
    build_prompt: PromptBuilder
    rag_query: str | None = None
    web_query: QueryBuilder | None = None
    web_results: int = 5
    requires: tuple[str, ...] = ("venture_profile",)
    postprocess: PostProcess | None = None
    max_self_corrections: int = 1
    extra: dict = field(default_factory=dict)


def as_json(obj: Any) -> str:
    return json.dumps(obj, indent=1, default=str)


def _upstream_failed(state: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = state.get(k)
        if v is None or (isinstance(v, dict) and "error" in v):
            return k
    return None


async def _gather_context(spec: AgentSpec, state: dict) -> AgentContext:
    ctx = AgentContext()
    if spec.rag_query and state.get("deck_indexed"):
        chunks = await get_store().search(state["session_id"], spec.rag_query, k=4)
        ctx.deck = format_context(chunks)
    if spec.web_query:
        ctx.web = await search_market_data(spec.web_query(state), max_results=spec.web_results)
    return ctx


RETRY_DELAY_S = 2.0


async def _invoke_with_retry(llm, prompt: str, name: str, attempts: int = 4):
    """Retry transient failures (malformed output, rate limits, timeouts) with backoff.

    Groq's free tier allows ~8k tokens per minute per model, so a 12-agent run can hit
    429s; Groq says how long to wait ("try again in 7.5s") and we honour that.
    """
    for i in range(attempts):
        try:
            return await llm.ainvoke(prompt)
        except Exception as exc:
            if i == attempts - 1:
                raise
            msg = str(exc)
            m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", msg)
            wait = (int(m.group(1) or 0) * 60 + float(m.group(2)) + 1) if m else RETRY_DELAY_S * (i + 1)
            wait = min(wait, 65) if RETRY_DELAY_S else 0
            logger.warning("%s: model call failed (%s); retrying in %.0fs", name, msg[:160], wait)
            await asyncio.sleep(wait)


def make_node(spec: AgentSpec) -> Callable[[dict], Awaitable[dict]]:
    async def node(state: dict) -> dict:
        started = time.perf_counter()
        trace = {"agent": spec.name, "number": spec.number, "output": spec.output_key,
                 "attempts": 0, "status": "ok"}

        missing = _upstream_failed(state, spec.requires)
        if missing:
            trace.update(status="skipped", error=f"upstream '{missing}' unavailable", ms=0)
            return {spec.output_key: {"error": f"skipped: {missing} unavailable"}, "trace": [trace]}

        try:
            ctx = await _gather_context(spec, state)
            prompt = spec.build_prompt(state, ctx)
            llm = get_structured_llm(spec.role, spec.schema)

            checks = []
            for attempt in range(1 + spec.max_self_corrections):
                trace["attempts"] = attempt + 1
                parsed = await _invoke_with_retry(llm, prompt, spec.name)
                if not isinstance(parsed, spec.schema):  # some providers return dicts
                    parsed = spec.schema.model_validate(parsed)
                update = (spec.postprocess(parsed, state) if spec.postprocess
                          else {spec.output_key: parsed.model_dump()})
                checks = run_checks(spec.output_key, update[spec.output_key], {**state, **update})
                hard_failures = [c for c in checks if not c.passed and c.severity == "error"]
                if not hard_failures:
                    break
                feedback = "; ".join(f"{c.name}: {c.detail}" for c in hard_failures)
                logger.info("%s self-correcting (%s)", spec.name, feedback)
                prompt += (f"\n\nYOUR PREVIOUS ANSWER FAILED THESE QUALITY CHECKS: {feedback}.\n"
                           "Fix these problems in your new answer.")

            trace["ms"] = round((time.perf_counter() - started) * 1000)
            trace["checks_passed"] = sum(c.passed for c in checks)
            trace["checks_total"] = len(checks)
            return {**update, "trace": [trace], "evaluations": [c.to_dict() for c in checks]}

        except Exception as exc:  # one agent failing must not kill the run
            logger.exception("%s failed", spec.name)
            trace.update(status="error", error=f"{type(exc).__name__}: {exc}"[:500],
                         ms=round((time.perf_counter() - started) * 1000))
            return {spec.output_key: {"error": trace["error"]}, "trace": [trace]}

    node.__name__ = f"agent_{spec.number:02d}_{spec.output_key}"
    node.spec = spec  # type: ignore[attr-defined]
    return node
