"""Claim verification: are the deck's headline numbers real?

Founders inflating numbers is what investors care about most, so this agent
does three things, two of them in code:

1. **Extract** (fast model): up to 8 headline quantitative claims, each with the
   slide it is on and an exact quote.
2. **Check the citation** (code): the quote must actually appear on that slide.
   A claim whose quote is not on the slide is marked ``citation_not_found``; the
   model invented or misattributed it.
3. **Cross-check market claims** (search + reasoning model): TAM and market
   growth are compared with independent sources ("Deck claims a $50B TAM; three
   sources put it at $6-9B"). Internal metrics such as revenue cannot be checked
   from outside and are labelled so rather than guessed.
"""
from __future__ import annotations

import logging
import re
import time

from agents.base import AgentSpec, _skip, as_json
from core import tools
from core.llm import call_structured
from core.telemetry import AGENT_DURATION, AGENT_RUNS, span
from schema import ClaimList, VerdictList
from services.security import SECURITY_PREAMBLE, defenses_enabled, fence

logger = logging.getLogger("agents")

EXTERNAL_KINDS = {"market_size", "market_growth"}
MAX_SEARCHES = 3


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())


def citation_found(quote: str, page: str) -> bool:
    """True if the quoted words (and its numbers) really are on the page."""
    q, p = _tokens(quote), set(_tokens(page))
    if not q:
        return False
    numbers = [t for t in q if any(ch.isdigit() for ch in t)]
    return sum(t in p for t in q) / len(q) >= 0.85 and all(n in p for n in numbers)


def extract_prompt(state: dict) -> str:
    pages = state.get("deck_pages", [])
    deck = "\n".join(f"[slide {n}] {text}" for n, text in enumerate(pages, start=1) if text.strip())
    return f"""You are a due-diligence analyst. List the headline quantitative claims this pitch deck makes
(market size, market growth, revenue, growth rate, customers, users, funding, pricing).

Startup: {as_json(state.get('venture_profile', {}))}
Deck text by slide:
{fence('pitch deck', deck[:12000])}

For each claim give the slide number, copy the exact words from that slide in `quote`, and write a
short search query that would find independent figures for it. Only include claims that are actually
in the deck."""


def verify_prompt(state: dict, claims: list[dict], research: dict[int, str]) -> str:
    items = "\n\n".join(f"Claim {i}: {claims[i]['claim']} (stated value: {claims[i]['value']})\n"
                        f"Search results:\n{research[i]}" for i in research)
    return f"""You are a skeptical due-diligence analyst. For each claim, compare the stated value with
what independent sources in its search results say.

Startup: {as_json(state.get('venture_profile', {}))}

{items}

status: "consistent" if the sources roughly agree (within ~2x), "conflict" if they clearly disagree,
"unverifiable" if the results do not address it. Put the independent estimate in external_estimate,
cite only URLs that appear in the search results, and explain in one sentence."""


def _query(state: dict, claim: dict) -> str:
    """The extractor's own short query; long profile text returns nothing from search engines."""
    q = (claim.get("search_query") or "").strip()
    if q:
        return q[:120]
    what = "market size" if claim["kind"] == "market_size" else "market growth rate CAGR"
    return f"{state.get('venture_profile', {}).get('business_model', '')} {what}"[:120]


async def verification_node(state: dict) -> dict:
    started = time.perf_counter()
    spec = SPEC
    trace = {"agent": spec.name, "number": spec.number, "output": spec.output_key, "attempts": 1, "status": "ok"}
    if not state.get("deck_pages"):
        return _skip(spec, "no pitch deck to verify", trace)
    if "error" in (state.get("venture_profile") or {"error": 1}):
        return _skip(spec, "upstream 'venture_profile' unavailable", trace)

    with span("agent.claims", **{"agent.name": spec.name}):
        try:
            pre = SECURITY_PREAMBLE if defenses_enabled() else ""
            extracted, u1 = await call_structured("fast", ClaimList, pre + extract_prompt(state), spec.name)
            pages = state["deck_pages"]
            claims = []
            for c in extracted.claims[:8]:
                d = c.model_dump()
                page = pages[c.slide - 1] if 1 <= c.slide <= len(pages) else ""
                d["citation"] = "verified" if citation_found(c.quote, page) else "citation_not_found"
                d.update(status="unverifiable", external_estimate="", sources=[],
                         note="internal metric; cannot be checked against public sources")
                claims.append(d)

            research, web_sources = {}, []
            for i, d in enumerate(claims):
                if d["kind"] in EXTERNAL_KINDS and d["citation"] == "verified" and len(research) < MAX_SEARCHES:
                    res = await tools.call(spec.name, spec.tools, "web_search",
                                           {"query": _query(state, d), "max_results": 5})
                    text, sources = res["text"], res["sources"]
                    if res.get("error"):
                        trace.setdefault("warnings", []).append(res["error"])
                    if sources:
                        research[i] = fence("web search results", text)
                        web_sources += sources
                    else:
                        d["note"] = "no independent sources found"
            models = [f"{u1.provider}:{u1.model}"]
            if research:
                verdicts, u2 = await call_structured("reasoning", VerdictList,
                                                     pre + verify_prompt(state, claims, research), spec.name)
                models.append(f"{u2.provider}:{u2.model}")
                allowed = {s["url"] for s in web_sources}
                for v in verdicts.verdicts:
                    if v.index in research:
                        claims[v.index].update(status=v.status, external_estimate=v.external_estimate,
                                               sources=[u for u in v.sources if u in allowed], note=v.note)
            for d in claims:
                if d["citation"] == "citation_not_found":
                    d.update(status="unverifiable", note="the quoted words are not on the cited slide")

            summary = {k: sum(d["status"] == k for d in claims) for k in ("consistent", "conflict", "unverifiable")}
            summary["citations_not_found"] = sum(d["citation"] != "verified" for d in claims)
            elapsed = time.perf_counter() - started
            trace.update(ms=round(elapsed * 1000), models=sorted(set(models)), checks_passed=0, checks_total=0)
            AGENT_RUNS.labels(spec.name, "ok").inc()
            AGENT_DURATION.labels(spec.name).observe(elapsed)
            evidence = {"claims": {"deck": [{"slide": d["slide"], "excerpt": d["quote"][:160]} for d in claims],
                                   "web": web_sources}}
            return {"claims": {"claims": claims, "summary": summary}, "trace": [trace], "evidence": evidence}
        except Exception as exc:
            logger.exception("%s failed", spec.name)
            trace.update(status="error", error=f"{type(exc).__name__}: {exc}"[:500],
                         ms=round((time.perf_counter() - started) * 1000))
            AGENT_RUNS.labels(spec.name, "error").inc()
            return {"claims": {"error": trace["error"]}, "trace": [trace]}


SPEC = AgentSpec(
    number=13, name="Claim Verification", output_key="claims", schema=ClaimList, role="fast",
    build_prompt=lambda s, c: "", optional=True, est_cost_usd=0.004, extra_tools=("web_search",),
    description="Extracts the deck's headline numbers, checks each citation, cross-checks market claims "
                "against independent sources. Only useful when a pitch deck was provided.",
)
claim_verification_agent = verification_node
claim_verification_agent.spec = SPEC  # type: ignore[attr-defined]
