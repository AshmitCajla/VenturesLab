"""The agent graph, wired by real data dependencies.

    understand -> plan -> market_research ----> pestle*, market_sizing
                       -> competition --------> swot* (+ market) -> porter*
                       -> customer_validation
                       -> claims*  (verification)
    [market_sizing, customer, porter, pestle, claims] -> scoring
    scoring >= threshold ? [roadmap || gtm || financials] : END

Agents marked * are optional: the planner decides whether they run. A node
with several inputs waits for all of them (LangGraph join), and every agent
starts as soon as its own inputs are ready, so independent agents run in parallel.
"""
from __future__ import annotations

from functools import wraps

from langgraph.graph import END, StateGraph

from agents.agent_01_understanding import venture_understanding_agent
from agents.agent_02_market import market_research_agent
from agents.agent_03_competition import competition_agent
from agents.agent_04_swot import swot_agent
from agents.agent_05_pestle import pestle_agent
from agents.agent_06_porter import porter_agent
from agents.agent_07_customer import customer_validation_agent
from agents.agent_08_market_sizing import market_sizing_agent
from agents.agent_09_scoring import venture_scoring_agent
from agents.agent_10_roadmap import mvp_roadmap_agent
from agents.agent_11_gtm import gtm_strategy_agent
from agents.agent_12_financials import financial_model_agent
from agents.agent_13_verification import claim_verification_agent
from agents.base import _skip
from core.config import get_settings
from workflow.planner import plan_node, selected
from workflow.state import VentureState

NODES = {
    "understand": venture_understanding_agent,
    "market_research": market_research_agent,
    "competition": competition_agent,
    "customer_validation": customer_validation_agent,
    "claims": claim_verification_agent,
    "swot": swot_agent,
    "pestle": pestle_agent,
    "porter": porter_agent,
    "market_sizing": market_sizing_agent,
    "scoring": venture_scoring_agent,
    "roadmap": mvp_roadmap_agent,
    "gtm": gtm_strategy_agent,
    "financials": financial_model_agent,
}

# Public metadata used by the API / UI to show progress
AGENTS = [{"node": n, "number": f.spec.number, "name": f.spec.name, "optional": f.spec.optional}
          for n, f in sorted(NODES.items(), key=lambda kv: kv[1].spec.number)]


def _planned(key: str, fn):
    """Wrap an optional agent so it is skipped when the planner did not select it."""
    if not fn.spec.optional:
        return fn

    @wraps(fn)
    async def node(state: dict) -> dict:
        run, reason = selected(state, key)
        if not run:
            trace = {"agent": fn.spec.name, "number": fn.spec.number, "output": fn.spec.output_key,
                     "attempts": 0, "status": "ok"}
            out = _skip(fn.spec, f"not planned: {reason}", trace)
            out[fn.spec.output_key] = {"skipped": reason}
            return out
        return await fn(state)
    return node


async def _noop(state):  # synchronisation barriers
    return {}


def route_after_scoring(state) -> str:
    return "build" if state.get("score", 0.0) >= get_settings().build_threshold else "skip"


def build_graph():
    g = StateGraph(VentureState)
    for name, fn in NODES.items():
        g.add_node(name, _planned(name, fn))
    g.add_node("plan", plan_node)
    g.add_node("start_builders", _noop)
    g.add_node("join_builders", _noop)

    g.set_entry_point("understand")
    g.add_edge("understand", "plan")
    for n in ("market_research", "competition", "customer_validation", "claims"):
        g.add_edge("plan", n)
    g.add_edge("market_research", "pestle")
    g.add_edge("market_research", "market_sizing")
    g.add_edge(["market_research", "competition"], "swot")
    g.add_edge(["competition", "swot"], "porter")
    g.add_edge(["market_sizing", "customer_validation", "porter", "pestle", "claims"], "scoring")
    g.add_conditional_edges("scoring", route_after_scoring, {"build": "start_builders", "skip": END})
    for b in ("roadmap", "gtm", "financials"):
        g.add_edge("start_builders", b)
    g.add_edge(["roadmap", "gtm", "financials"], "join_builders")
    g.add_edge("join_builders", END)
    return g.compile()


compiled_graph = build_graph()
