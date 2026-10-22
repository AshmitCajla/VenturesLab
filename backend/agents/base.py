"""Shared machinery for all agents.

Each agent is declared as an ``AgentSpec`` (what it reads, what it asks,
what it returns). ``make_node`` turns a spec into a LangGraph node that:

1. skips cleanly if an upstream dependency failed, or if the agent is optional
   and the run's cost budget cannot cover it,
2. retrieves the pitch-deck passages relevant to *this* agent (RAG),
3. optionally runs a live web search,
   (deck and web text are fenced as untrusted data, see services/security.py)
4. calls the LLM through ``core.llm`` (provider chain, retries, usage and cost),
5. runs the agent's evaluation checks and, if a hard check fails, retries
   once with the failure fed back to the model (self-correction),
6. records timing, model, tokens, cost, sources and check results.

The slides and URLs that were in front of the agent are stored under
``evidence[output_key]``, so every section of the report links to its sources.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from core import runctx, tools
from core.llm import call_structured
from core.model_router import Role
from core.telemetry import AGENT_DURATION, AGENT_RUNS, BUDGET_SKIPS, CHECKS, span
from evaluation.checks import run_checks
from services.documents import Chunk
from services.retrieval import format_context
from services.security import SECURITY_PREAMBLE, defenses_enabled, fence

logger = logging.getLogger("agents")

PromptBuilder = Callable[[dict, "AgentContext"], str]
PostProcess = Callable[[BaseModel, dict], dict]
QueryBuilder = Callable[[dict], str]


@dataclass
class AgentContext:
    deck: str = ""
    web: str = ""
    deck_sources: list[dict] = field(default_factory=list)
    web_sources: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # degraded inputs, surfaced in the trace


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
    optional: bool = False  # may be skipped by the planner or to stay within budget
    est_cost_usd: float = 0.002  # reserve checked against the budget before optional agents run
    description: str = ""  # shown to the planner
    extra_tools: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def tools(self) -> tuple[str, ...]:
        """Allow-list enforced by core.tools: only what this agent's spec says it needs."""
        return (
            (("deck_search",) if self.rag_query else ())
            + (("web_search",) if self.web_query else ())
            + self.extra_tools
        )


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
        res = await tools.call(
            spec.name, spec.tools, "deck_search", {"session_id": state["session_id"], "query": spec.rag_query, "k": 4}
        )
        chunks = [Chunk(text=c["text"], page=c["slide"], score=c["score"]) for c in res["chunks"]]
        ctx.deck = fence("pitch-deck excerpts", format_context(chunks))
        ctx.deck_sources = [
            {"slide": c.page, "score": round(c.score, 3) if c.score is not None else None, "excerpt": c.text[:160]}
            for c in chunks
        ]
    if spec.web_query:
        res = await tools.call(
            spec.name, spec.tools, "web_search", {"query": spec.web_query(state), "max_results": spec.web_results}
        )
        ctx.web, ctx.web_sources = fence("web search results", res["text"]), res["sources"]
        if res.get("error"):
            ctx.warnings.append(res["error"])
    return ctx


def _skip(spec: AgentSpec, reason: str, trace: dict) -> dict:
    trace.update(status="skipped", error=reason, ms=0)
    AGENT_RUNS.labels(spec.name, "skipped").inc()
    return {spec.output_key: {"error": f"skipped: {reason}"}, "trace": [trace]}


def make_node(spec: AgentSpec) -> Callable[[dict], Awaitable[dict]]:
    async def node(state: dict) -> dict:
        started = time.perf_counter()
        trace = {"agent": spec.name, "number": spec.number, "output": spec.output_key, "attempts": 0, "status": "ok"}

        missing = _upstream_failed(state, spec.requires)
        if missing:
            return _skip(spec, f"upstream '{missing}' unavailable", trace)
        run = runctx.current()
        if spec.optional and run and run.over_budget(spec.est_cost_usd):
            BUDGET_SKIPS.labels(spec.name).inc()
            return _skip(spec, f"cost budget (${run.budget_usd:.2f}) reached", trace)

        spent_before = run.spent_usd if run else 0.0
        with span(f"agent.{spec.output_key}", **{"agent.name": spec.name, "agent.number": spec.number}) as s:
            try:
                ctx = await _gather_context(spec, state)
                prompt = spec.build_prompt(state, ctx)
                if defenses_enabled():
                    prompt = SECURITY_PREAMBLE + prompt

                checks, models, recovered = [], [], []
                for attempt in range(1 + spec.max_self_corrections):
                    trace["attempts"] = attempt + 1
                    parsed, usage = await call_structured(spec.role, spec.schema, prompt, spec.name)
                    models.append(f"{usage.provider}:{usage.model}")
                    recovered += usage.recovered
                    update = (
                        spec.postprocess(parsed, state) if spec.postprocess else {spec.output_key: parsed.model_dump()}
                    )
                    checks = run_checks(spec.output_key, update[spec.output_key], {**state, **update})
                    hard_failures = [c for c in checks if not c.passed and c.severity == "error"]
                    if not hard_failures:
                        break
                    feedback = "; ".join(f"{c.name}: {c.detail}" for c in hard_failures)
                    logger.info("%s self-correcting (%s)", spec.name, feedback)
                    prompt += (
                        f"\n\nYOUR PREVIOUS ANSWER FAILED THESE QUALITY CHECKS: {feedback}.\n"
                        "Fix these problems in your new answer."
                    )

                elapsed = time.perf_counter() - started
                if ctx.warnings:
                    trace["warnings"] = ctx.warnings
                if recovered:
                    trace["recovered_errors"] = recovered[:10]
                trace.update(
                    ms=round(elapsed * 1000),
                    models=sorted(set(models)),
                    checks_passed=sum(c.passed for c in checks),
                    checks_total=len(checks),
                    cost_usd=round((run.spent_usd - spent_before) if run else 0.0, 6),
                )
                for c in checks:
                    CHECKS.labels(spec.output_key, str(c.passed).lower()).inc()
                AGENT_RUNS.labels(spec.name, "ok").inc()
                AGENT_DURATION.labels(spec.name).observe(elapsed)
                evidence = {spec.output_key: {"deck": ctx.deck_sources, "web": ctx.web_sources}}
                return {**update, "trace": [trace], "evaluations": [c.to_dict() for c in checks], "evidence": evidence}

            except Exception as exc:  # one agent failing must not kill the run
                logger.exception("%s failed", spec.name)
                s.set_attribute("error", True)
                trace.update(
                    status="error",
                    error=f"{type(exc).__name__}: {exc}"[:500],
                    ms=round((time.perf_counter() - started) * 1000),
                )
                AGENT_RUNS.labels(spec.name, "error").inc()
                return {spec.output_key: {"error": trace["error"]}, "trace": [trace]}

    node.__name__ = f"agent_{spec.number:02d}_{spec.output_key}"
    node.spec = spec  # type: ignore[attr-defined]
    return node
