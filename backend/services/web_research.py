"""Live web search used by the market and competition agents."""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=256)
def _search_sync(query: str, max_results: int) -> tuple[dict, ...]:
    from ddgs import DDGS

    return tuple(DDGS().text(query, max_results=max_results) or [])


async def search_market_data(query: str, max_results: int = 5) -> str:
    """Returns search snippets as plain text, or a note when search is unavailable.

    Never raises: a flaky search provider should degrade an agent's answer,
    not crash the whole pipeline.
    """
    if not get_settings().web_search_enabled:
        return "Web search disabled; rely on general knowledge and state assumptions."
    try:
        results = await asyncio.wait_for(asyncio.to_thread(_search_sync, query, max_results), 15)
    except Exception as exc:
        logger.warning("web search failed for %r: %s", query, exc)
        return "Web search unavailable; rely on general knowledge and state assumptions."
    if not results:
        return "No relevant web results found."
    return "\n\n".join(
        f"Title: {r.get('title')}\nSummary: {r.get('body')}\nSource: {r.get('href')}" for r in results
    )
