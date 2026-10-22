from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class VentureState(TypedDict, total=False):
    # inputs
    session_id: str
    idea: str
    url_content: str | None
    deck_indexed: bool
    deck_summary: str | None  # first slides, always given to the profile agent
    deck_pages: list[str]  # visible, cleaned text per slide (citations are checked against it)
    deck_hash: str  # identifies identical decks across runs (memory diff)
    security: dict  # hidden text / injections removed at ingestion (services/security.py)
    plan: dict  # which optional agents run, and why (workflow/planner.py)

    # agent outputs
    venture_profile: dict
    claims: dict  # headline deck claims, cited and verified (agents/agent_13_verification.py)
    market_analysis: dict
    competitors: list
    swot: dict
    pestle: dict
    porter: dict
    customer_validation: dict
    market_size: dict
    scoring: dict
    score: float
    recommendation: str
    roadmap: dict | None
    gtm_strategy: dict | None
    financial_model: dict | None

    # observability - parallel branches append, LangGraph merges with the reducer
    trace: Annotated[list, operator.add]
    evaluations: Annotated[list, operator.add]
    evidence: Annotated[dict, operator.or_]  # output_key -> {deck: [...], web: [...]}
