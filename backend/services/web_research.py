"""Live web search used by the research agents and the verification agent."""
from __future__ import annotations

import asyncio
import logging

from core import chaos
from core.config import get_settings
from core.telemetry import timed_tool
from services import cache

logger = logging.getLogger(__name__)


def _search_sync(query: str, max_results: int) -> list[dict]:
    from ddgs import DDGS

    return list(DDGS().text(query, max_results=max_results) or [])


async def search(query: str, max_results: int = 5) -> list[dict]:
    """[{title, body, href}]. Cached for a week. Raises on failure."""
    payload = {"q": query, "n": max_results}
    if (hit := await cache.get("search", payload)) is not None:
        return hit
    with timed_tool("web_search"):
        chaos.before_search()
        try:
            results = await asyncio.wait_for(asyncio.to_thread(_search_sync, query, max_results), 15)
        except Exception as exc:
            if "no results" in str(exc).lower():  # an empty answer is not an outage
                return []
            raise
    results = [{"title": r.get("title"), "body": r.get("body"), "href": r.get("href")} for r in results]
    if results:
        await cache.put("search", payload, results)
    return results


def format_results(results: list[dict]) -> str:
    return "\n\n".join(f"Title: {r['title']}\nSummary: {r['body']}\nSource: {r['href']}" for r in results)


async def search_market_data(query: str, max_results: int = 5) -> tuple[str, list[dict], str | None]:
    """(prompt text, sources used, error). Never raises: a flaky search provider should
    degrade an agent's answer, not crash the pipeline; the error is reported so the
    degradation is visible in the trace and to the ops agent.
    """
    if not get_settings().web_search_enabled:
        return "Web search disabled; rely on general knowledge and state assumptions.", [], None
    try:
        results = await search(query, max_results)
    except Exception as exc:
        logger.warning("web search failed for %r: %s", query, exc)
        return ("Web search unavailable; rely on general knowledge and state assumptions.", [],
                f"web search unavailable: {type(exc).__name__}: {str(exc)[:200]}")
    if not results:
        return "No relevant web results found.", [], None
    return format_results(results), [{"title": r["title"], "url": r["href"]} for r in results], None
