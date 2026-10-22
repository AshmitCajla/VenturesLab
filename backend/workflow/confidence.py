"""Per-section confidence from evidence coverage, computed in code.

The model is never asked how confident it is. Each section starts at 0.5 and
moves with things that can be counted: was relevant deck text retrieved, how
many independent web sources were in front of the agent, did its quality checks
pass, did it need a fallback model, and (for the verdict) how many deck claims
conflicted with outside sources. When evidence is thin the report says so.

These are heuristics for "how much should a reader lean on this section", not
calibrated probabilities; ``evaluation/consistency.py`` measures how stable
sections actually are.
"""
from __future__ import annotations

SECTIONS = ("venture_profile", "market_analysis", "competitors", "customer_validation", "swot",
            "pestle", "porter", "market_size", "scoring", "roadmap", "gtm_strategy", "financial_model")
WEB_SECTIONS = {"market_analysis", "competitors"}
DECK_RELEVANCE_MIN = 0.35


def _level(x: float) -> str:
    return "High" if x >= 0.7 else "Medium" if x >= 0.45 else "Low"


def section_confidence(key: str, state: dict) -> dict | None:
    value = state.get(key)
    if value is None:
        return None
    if isinstance(value, dict) and ("error" in value or "skipped" in value):
        return {"level": "Unavailable", "score": 0.0, "reasons": [value.get("error") or value.get("skipped")]}

    score, reasons = 0.5, []
    ev = (state.get("evidence") or {}).get(key, {})
    deck_hits = [s for s in ev.get("deck", []) if (s.get("score") or 0) >= DECK_RELEVANCE_MIN]
    if state.get("deck_indexed"):
        if deck_hits:
            score += 0.15
            reasons.append(f"grounded in slides {sorted({s['slide'] for s in deck_hits})}")
        elif ev.get("deck") is not None and ev.get("deck") != []:
            score -= 0.1
            reasons.append("the deck has little on this topic")
    elif key not in WEB_SECTIONS:
        score -= 0.1
        reasons.append("no pitch deck: based on the description only")
    if key in WEB_SECTIONS:
        n = len(ev.get("web", []))
        score += 0.2 if n >= 3 else 0.05 if n else -0.15
        reasons.append(f"{n} web source(s)" if n else "no web sources (search unavailable)")

    checks = [c for c in state.get("evaluations", []) if c.get("agent") == key]
    failed = [c for c in checks if not c["passed"]]
    if checks and not failed:
        score += 0.1
        reasons.append(f"all {len(checks)} quality checks passed")
    for c in failed:
        score -= 0.25 if c["severity"] == "error" else 0.1
        reasons.append(f"check failed: {c['name']}")

    trace = next((t for t in state.get("trace", []) if t.get("output") == key), {})
    if any(not m.startswith(("groq:", "fake:")) for m in trace.get("models", [])) and trace.get("models"):
        score -= 0.05
        reasons.append(f"answered by fallback model {', '.join(trace['models'])}")

    if key == "scoring":
        summary = (state.get("claims") or {}).get("summary") or {}
        if summary.get("conflict"):
            score -= 0.1 * summary["conflict"]
            reasons.append(f"{summary['conflict']} deck claim(s) conflict with independent sources")
        if summary.get("citations_not_found"):
            score -= 0.05 * summary["citations_not_found"]
            reasons.append(f"{summary['citations_not_found']} claim citation(s) not found on the slide")
        upstream = [section_confidence(k, state) for k in ("market_analysis", "market_size", "customer_validation")]
        ups = [u["score"] for u in upstream if u and u["level"] != "Unavailable"]
        if ups:
            score = (score + sum(ups) / len(ups)) / 2
            reasons.append("averaged with the confidence of its inputs")

    score = max(0.0, min(1.0, score))
    return {"level": _level(score), "score": round(score, 2), "reasons": reasons}


def report_confidence(state: dict) -> dict:
    return {k: c for k in SECTIONS if (c := section_confidence(k, state)) is not None}
