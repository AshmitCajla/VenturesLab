"""The 12-agent LangGraph workflow.

    understand -> market_research -> competition
        -> [swot || pestle]                       (parallel)
        -> [porter || customer_validation]        (parallel)
        -> market_sizing -> scoring
        -> score >= threshold ? [roadmap || gtm || financials] : END
"""
from __future__ import annotations

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
from core.config import get_settings
from workflow.state import VentureState

NODES = {
    "understand": venture_understanding_agent,
    "market_research": market_research_agent,
    "competition": competition_agent,
    "swot": swot_agent,
    "pestle": pestle_agent,
    "porter": porter_agent,
    "customer_validation": customer_validation_agent,
    "market_sizing": market_sizing_agent,
    "scoring": venture_scoring_agent,
    "roadmap": mvp_roadmap_agent,
    "gtm": gtm_strategy_agent,
    "financials": financial_model_agent,
}

# Public metadata used by the API / UI to show progress
AGENTS = [{"node": n, "number": f.spec.number, "name": f.spec.name} for n, f in NODES.items()]


async def _noop(state):  # synchronisation barriers for the parallel phases
    return {}


def route_after_scoring(state) -> str:
    return "build" if state.get("score", 0.0) >= get_settings().build_threshold else "skip"


def build_graph():
    g = StateGraph(VentureState)
    for name, fn in NODES.items():
        g.add_node(name, fn)
    for barrier in ("join_strategic", "join_validation", "start_builders", "join_builders"):
        g.add_node(barrier, _noop)

    g.set_entry_point("understand")
    g.add_edge("understand", "market_research")
    g.add_edge("market_research", "competition")

    g.add_edge("competition", "swot")
    g.add_edge("competition", "pestle")
    g.add_edge(["swot", "pestle"], "join_strategic")

    g.add_edge("join_strategic", "porter")
    g.add_edge("join_strategic", "customer_validation")
    g.add_edge(["porter", "customer_validation"], "join_validation")

    g.add_edge("join_validation", "market_sizing")
    g.add_edge("market_sizing", "scoring")
    g.add_conditional_edges("scoring", route_after_scoring, {"build": "start_builders", "skip": END})

    for b in ("roadmap", "gtm", "financials"):
        g.add_edge("start_builders", b)
    g.add_edge(["roadmap", "gtm", "financials"], "join_builders")
    g.add_edge("join_builders", END)
    return g.compile()


compiled_graph = build_graph()
