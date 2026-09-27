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

    # agent outputs
    venture_profile: dict
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

    # observability - parallel branches append, LangGraph merges with operator.add
    trace: Annotated[list, operator.add]
    evaluations: Annotated[list, operator.add]
