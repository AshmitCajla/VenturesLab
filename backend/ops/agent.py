"""The ops agent: diagnoses incidents and answers "what broke last night?".

It reaches telemetry only through the ops MCP server (``mcp_servers/ops_server``),
the same interface a human gets in Claude Desktop. For an alert it gathers a
fixed evidence pack (SLOs, error signatures, agent health, logs, failed runs,
Prometheus), then the reasoning model writes a diagnosis that must cite that
evidence and link a runbook. For free-form questions the fast model first picks
which tools to call, then the reasoning model answers from their output.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from core.llm import call_structured
from core.telemetry import span

logger = logging.getLogger("ops")

Category = Literal[
    "rate_limit",
    "llm_timeout",
    "llm_provider_outage",
    "web_search",
    "deck_parsing",
    "model_output_format",
    "restart",
    "cost_budget",
    "capacity",
    "code_bug",
    "unknown",
]

RUNBOOK_FOR = {
    "rate_limit": "llm-provider-errors",
    "llm_timeout": "high-latency",
    "llm_provider_outage": "llm-provider-errors",
    "web_search": "web-search-down",
    "deck_parsing": "deck-parsing-failures",
    "model_output_format": "llm-provider-errors",
    "cost_budget": "cost-budget",
    "restart": "run-failures",
    "capacity": "high-latency",
    "code_bug": "run-failures",
    "unknown": "run-failures",
}


class Diagnosis(BaseModel):
    summary: str = Field(description="one sentence: what is happening and how bad it is")
    likely_cause: str = Field(description="the most likely cause, specific (which provider, agent, error)")
    category: Category
    evidence: list[str] = Field(description="3-6 facts from the tool output that support the cause, with numbers")
    confidence: Literal["High", "Medium", "Low"]
    next_steps: list[str] = Field(description="concrete actions for the on-call engineer, most urgent first")


class ToolCall(BaseModel):
    tool: str
    arguments: dict = Field(default_factory=dict)


class ToolPlan(BaseModel):
    calls: list[ToolCall] = Field(description="1-4 tool calls that would answer the question")


class OpsAnswer(BaseModel):
    answer: str = Field(description="direct answer with numbers and times")
    tools_used: list[str]


class Postmortem(BaseModel):
    markdown: str = Field(description="blameless postmortem in Markdown")


async def _call(client, tool: str, args: dict | None = None):
    from core.tools import decode

    res = await client.call_tool(tool, args or {})
    if res.is_error:
        return {"tool_error": "".join(getattr(c, "text", "") for c in res.content)[:300]}
    return decode(res)


def _client():
    from mcp import Client

    from mcp_servers.ops_server import server

    return Client(server)


EVIDENCE_PACK = [
    ("slo_status", {}),
    ("error_signatures", {"hours": 1}),
    ("agent_health", {"hours": 1}),
    ("recent_runs", {"hours": 2, "status": "failed", "limit": 8}),
    ("recent_logs", {"level": "WARNING", "minutes": 30, "limit": 25}),
    ("model_health", {}),
    ("query_prometheus", {"promql": "sum by (provider, model, outcome) (increase(ventureslab_llm_calls_total[15m]))"}),
]


async def gather_evidence() -> dict:
    async with _client() as client:
        return {name: await _call(client, name, args) for name, args in EVIDENCE_PACK}


def signals(evidence: dict) -> list[str]:
    sigs = evidence.get("error_signatures") or []
    if not isinstance(sigs, list):
        return []
    return [f"{s['count']}x {s['category']}: {s['signature'][:120]}" for s in sigs[:5]]


NEXT_STEPS = {
    "restart": [
        "Check whether a deploy or restart happened at the failure time",
        "Give the API a shutdown grace period (SHUTDOWN_GRACE_S, container stop timeout)",
    ],
    "rate_limit": [
        "Check provider quotas (TPM/TPD) in recent_logs",
        "Lower MAX_CONCURRENT_JOBS or switch LLM_PROVIDER",
    ],
    "llm_timeout": ["Check provider latency in the Ops dashboard", "Lower LLM_TIMEOUT_S so fallbacks start sooner"],
    "llm_provider_outage": [
        "Check the provider status page and the model_health canary",
        "Make the fallback primary (LLM_PROVIDER) until it recovers",
    ],
    "web_search": ["Check egress/DNS from the API host", "Set WEB_SEARCH=0 temporarily"],
    "deck_parsing": ["Find the affected decks in recent_runs", "Check for a PyMuPDF regression"],
    "model_output_format": ["Check whether FAST_MODEL/REASONING_MODEL changed", "Roll back the model change"],
}


def rule_based_diagnosis(alert: dict, evidence: dict, reason: str) -> Diagnosis:
    """Used when the reasoning model is unavailable, which during an LLM incident is likely:
    incident tooling must not depend on the thing that is down."""
    sigs = evidence.get("error_signatures") if isinstance(evidence.get("error_signatures"), list) else []
    top = sigs[0] if sigs else None
    category = top["category"] if top else "unknown"
    return Diagnosis(
        summary=f"{alert.get('labels', {}).get('alertname', 'Alert')} fired; automatic diagnosis from error "
        f"signatures only (model unavailable: {reason[:120]}).",
        likely_cause=(
            f"{top['count']}x {top['signature'][:160]} (agents: {', '.join(top['agents'])})"
            if top
            else "no error signatures in the last hour"
        ),
        category=category,
        evidence=signals(evidence) or ["no errors recorded in the last hour"],
        confidence="Low",
        next_steps=NEXT_STEPS.get(category, ["Start with the run-failures runbook"]),
    )


async def diagnose(alert: dict, evidence: dict | None = None) -> tuple[Diagnosis, dict]:
    with span("ops.diagnose", alert=alert.get("labels", {}).get("alertname")):
        evidence = evidence or await gather_evidence()
        try:
            return await _model_diagnosis(alert, evidence), evidence
        except Exception as exc:
            logger.warning("model diagnosis failed (%s); falling back to rules", str(exc)[:160])
            return rule_based_diagnosis(alert, evidence, f"{type(exc).__name__}: {exc}"), evidence


async def _model_diagnosis(alert: dict, evidence: dict) -> Diagnosis:
    prompt = f"""You are the on-call SRE for VenturesLab, a multi-agent LLM service that analyses
pitch decks (Groq and OpenRouter models, web search, PDF parsing). An alert fired. Diagnose the most
likely cause from the evidence only. Cite numbers from the evidence.
- The error messages of failed runs and the error signatures are the primary evidence of cause.
  Something else being unhealthy at the same time (e.g. one fallback model down) is not the cause
  unless the failed runs' errors point to it. "interrupted by a server restart" means a deploy or
  restart killed runs in progress (category restart).
- Confidence High only when the failed runs, the signatures and the metrics all point to the same
  cause; Medium when they mostly agree; Low when evidence is thin or conflicting.
- Mention other unhealthy components you saw as secondary findings in next_steps.

Alert: {json.dumps(alert, default=str)[:1500]}

Pre-computed signals (error signature -> category hint): {json.dumps(signals(evidence))}

Evidence from the ops tools:
{json.dumps(evidence, default=str)[:14000]}"""
    diag, _ = await call_structured("reasoning", Diagnosis, prompt, "Ops diagnosis", attempts=3)
    return diag


async def ask_ops(question: str) -> OpsAnswer:
    with span("ops.ask"):
        async with _client() as client:
            listing = await client.list_tools()
            menu = "\n".join(
                f"- {t.name}{json.dumps(t.input_schema.get('properties', {}))[:200]}: "
                f"{(t.description or '').splitlines()[0] if t.description else ''}"
                for t in listing.tools
            )
            plan, _ = await call_structured(
                "fast",
                ToolPlan,
                f"""Pick 1-4 ops tools to answer the question.
Tools:
{menu}

Question: {question}
Time words: "last night" ~ hours=16, "today" ~ hours=24, "this week" ~ hours=168.""",
                "Ops tool plan",
                attempts=2,
            )
            names = {t.name for t in listing.tools}
            results = {}
            for c in plan.calls[:4]:
                if c.tool in names:
                    results[f"{c.tool}({json.dumps(c.arguments)})"] = await _call(client, c.tool, c.arguments)
        answer, _ = await call_structured(
            "reasoning",
            OpsAnswer,
            f"""You are the on-call SRE for VenturesLab.
Answer the question from the tool output only, with numbers and times. If the data does not answer it,
say what is missing.

Question: {question}

Tool output:
{json.dumps(results, default=str)[:14000]}""",
            "Ops answer",
            attempts=3,
        )
        answer.tools_used = list(results)
        return answer


async def postmortem(incident: dict, evidence_after: dict) -> str:
    prompt = f"""Write a short blameless postmortem in Markdown for this resolved incident in VenturesLab.
Sections: Summary, Impact (runs and users affected, with numbers), Timeline, Root cause, Detection,
What went well, What went poorly, Action items (owner: TBD). Use only the data given; mark unknowns.

Incident: {json.dumps(incident, default=str)[:4000]}
Telemetry after resolution: {json.dumps(evidence_after, default=str)[:8000]}"""
    pm, _ = await call_structured("reasoning", Postmortem, prompt, "Postmortem", attempts=3)
    return pm.markdown
