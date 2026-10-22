"""Estimated LLM prices in USD per million tokens (input, output).

Used for the per-run cost budget and the cost dashboard. Groq prices are
on-demand list prices; ``:free`` OpenRouter models cost nothing. Unknown models
fall back to the most expensive known price, so budgets err on the safe side.
Override with PRICES_JSON='{"provider:model": [in, out]}'.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "groq:openai/gpt-oss-20b": (0.075, 0.30),
    "groq:openai/gpt-oss-120b": (0.15, 0.60),
    "groq:openai/gpt-oss-safeguard-20b": (0.075, 0.30),
    "openrouter:openai/gpt-oss-20b": (0.018, 0.09),
    "openrouter:openai/gpt-oss-120b": (0.15, 0.60),
}


@lru_cache
def prices() -> dict[str, tuple[float, float]]:
    table = dict(DEFAULT_PRICES)
    if os.getenv("PRICES_JSON"):
        table.update({k: tuple(v) for k, v in json.loads(os.environ["PRICES_JSON"]).items()})
    return table


def estimate_cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    if provider == "fake" or model.endswith(":free"):
        return 0.0
    table = prices()
    p_in, p_out = table.get(f"{provider}:{model}", max(table.values(), key=lambda p: p[1]))
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000
