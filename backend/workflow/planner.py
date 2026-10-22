"""Planner: decides which optional agents a venture actually needs.

Core agents (profile, market, competition, customer, sizing, scoring) always run:
the score depends on them, so the planner is not allowed to drop them. The
optional ones (SWOT, PESTLE, Porter, claim verification) are chosen by the fast
model from the venture profile, then constrained by rules in code:

* claim verification needs a deck,
* PESTLE is kept for regulated industries even if the model drops it,
* if the remaining budget cannot cover an agent's reserve, it is dropped.

The decision and its reasons are stored in ``state["plan"]`` and shown in the report.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from agents.base import as_json
from core import runctx
from core.config import get_settings
from core.llm import call_structured
from core.telemetry import span
from services.security import SECURITY_PREAMBLE, defenses_enabled, fence

logger = logging.getLogger("planner")

REGULATED = (
    "health",
    "medical",
    "clinic",
    "pharma",
    "bank",
    "fintech",
    "lending",
    "insurance",
    "payments",
    "crypto",
    "energy",
    "education",
    "child",
    "drone",
    "defense",
    "legal",
    "food",
)


class PlanChoice(BaseModel):
    agent: str
    run: bool
    reason: str = Field(description="one short sentence")


class Plan(BaseModel):
    choices: list[PlanChoice]


def optional_agents() -> dict[str, object]:
    from workflow.graph import NODES

    return {k: fn.spec for k, fn in NODES.items() if fn.spec.optional}


def _prompt(state: dict, options: dict) -> str:
    menu = "\n".join(f"- {k}: {spec.description}" for k, spec in options.items())
    pre = SECURITY_PREAMBLE if defenses_enabled() else ""
    return (
        pre
        + f"""You plan a venture-capital analysis. Core analyses always run. Decide which optional
analyses are worth their cost for THIS venture. Skip ones that would add little.

Venture: {fence("venture profile", as_json(state.get("venture_profile", {})))}
Pitch deck provided: {bool(state.get("deck_pages"))}

Optional analyses:
{menu}

Return one choice per optional analysis."""
    )


async def plan_node(state: dict) -> dict:
    options = optional_agents()
    decision = {k: {"run": True, "reason": "planner disabled; running every analysis"} for k in options}
    source = "default"
    if get_settings().planner_enabled and "error" not in (state.get("venture_profile") or {}):
        with span("planner"):
            try:
                plan, _ = await call_structured("fast", Plan, _prompt(state, options), "Planner", attempts=2)
                for c in plan.choices:
                    if c.agent in decision:
                        decision[c.agent] = {"run": c.run, "reason": c.reason}
                source = "model"
            except Exception as exc:  # planning is an optimisation; never fail the run over it
                logger.warning("planner failed, running everything: %s", exc)
                source = "fallback"

    profile_text = as_json(state.get("venture_profile", {})).lower()
    if "claims" in decision and not state.get("deck_pages"):
        decision["claims"] = {"run": False, "reason": "no pitch deck to verify"}
    if "pestle" in decision and any(w in profile_text for w in REGULATED) and not decision["pestle"]["run"]:
        decision["pestle"] = {"run": True, "reason": "regulated industry: regulation is material (rule)"}
    run = runctx.current()
    if run and run.budget_usd:
        reserve = 0.0
        for k, spec in options.items():
            if decision[k]["run"]:
                reserve += spec.est_cost_usd
                if run.over_budget(reserve + 0.02):  # keep headroom for the core agents
                    decision[k] = {"run": False, "reason": "dropped to stay within the run's cost budget"}
    return {"plan": {"source": source, "agents": decision}}


def selected(state: dict, key: str) -> tuple[bool, str]:
    entry = (state.get("plan") or {}).get("agents", {}).get(key)
    if entry is None:
        return True, ""
    return entry["run"], entry["reason"]
