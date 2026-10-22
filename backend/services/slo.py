"""SLO status computed from the run store.

Prometheus evaluates the same SLOs continuously (deploy/prometheus/rules.yml)
and pages on burn rate. This module answers the same questions from the
database, so ``GET /slo``, the ops agent and the weekly digest work even where
no Prometheus is running (e.g. a free-tier deploy).

Burn rate = (error rate in window) / (1 - SLO target). A burn rate of 1 spends
the error budget exactly over the SLO period; 14.4 over 1 hour spends 2% of a
30-day budget in that hour, which is the classic page threshold.
"""
from __future__ import annotations

import time

from core.config import get_settings
from services.db import get_db

WINDOWS = {"5m": 300, "1h": 3600, "6h": 6 * 3600, "1d": 86400, "30d": 30 * 86400}
# (long window, short window, burn-rate threshold, severity), per the Google SRE workbook
BURN_ALERTS = [("1h", "5m", 14.4, "page"), ("6h", "30m", 6.0, "page"), ("1d", "2h", 3.0, "ticket")]


async def _window(seconds: float) -> dict:
    since = time.time() - seconds
    rows = await get_db().fetchall(
        "select status, finished - created as secs, progress from runs where finished is not null and finished >= ? "
        "and source not in ('eval', 'redteam')", (since,))
    total = len(rows)
    failed = sum(r["status"] == "failed" for r in rows)
    degraded = sum(r["status"] == "done" and '"status": "error"' in (r["progress"] or "") for r in rows)
    durations = sorted(r["secs"] for r in rows if r["status"] == "done" and r["secs"] is not None)
    s = get_settings()
    slow = sum(d > s.slo_latency_target_s for d in durations)
    q = s.slo_latency_quantile
    p = durations[min(len(durations) - 1, int(q * len(durations)))] if durations else None
    return {"runs": total, "failed": failed, "degraded": degraded,
            "success_ratio": round(1 - failed / total, 4) if total else None,
            "complete_ratio": round(1 - (failed + degraded) / total, 4) if total else None,
            f"p{int(q * 100)}_s": round(p, 1) if p is not None else None,
            "slow_ratio": round(slow / len(durations), 4) if durations else None}


async def symptom_alerts(minutes: float = 15) -> list[dict]:
    """Degradations that do not break an SLO but need a ticket: search down, decks unreadable,
    model errors absorbed by retries and fallbacks."""
    rows = await get_db().fetchall(
        "select progress, warnings from runs where finished >= ? and source not in ('eval', 'redteam')",
        (time.time() - minutes * 60,))
    if not rows:
        return []
    search = sum("web search unavailable" in (r["progress"] or "") for r in rows)
    decks = sum("Pitch deck skipped" in (r["warnings"] or "") for r in rows)
    out = []
    # at least two affected runs: one failed search in one run is noise, not an incident
    if search >= 2 and search / len(rows) >= 0.5:
        out.append({"alert": "WebSearchDegraded", "severity": "ticket", "window": f"{minutes:.0f}m",
                    "affected_runs": search, "runs": len(rows)})
    absorbed = sum('"recovered_errors"' in (r["progress"] or "") for r in rows)
    if absorbed >= 2 and absorbed / len(rows) >= 0.5:
        out.append({"alert": "LLMErrorRateHigh", "severity": "ticket", "window": f"{minutes:.0f}m",
                    "affected_runs": absorbed, "runs": len(rows),
                    "note": "runs are completing only thanks to retries and fallbacks"})
    if decks >= 2:
        out.append({"alert": "DeckParsingFailures", "severity": "ticket", "window": f"{minutes:.0f}m",
                    "affected_runs": decks, "runs": len(rows)})
    return out


def _burn(error_ratio: float | None, target: float) -> float | None:
    return None if error_ratio is None else round(error_ratio / (1 - target), 2)


async def status() -> dict:
    s = get_settings()
    windows = {name: await _window(sec) for name, sec in {**WINDOWS, "30m": 1800, "2h": 7200}.items()}
    month = windows["30d"]
    budget_total = 1 - s.slo_success_target
    used = (month["failed"] / month["runs"]) / budget_total if month["runs"] else 0.0

    def err(w):
        return None if windows[w]["success_ratio"] is None else 1 - windows[w]["success_ratio"]

    alerts = []
    for long_w, short_w, threshold, severity in BURN_ALERTS:
        bl, bs = _burn(err(long_w), s.slo_success_target), _burn(err(short_w), s.slo_success_target)
        if bl is not None and bs is not None and bl >= threshold and bs >= threshold:
            alerts.append({"alert": "RunSuccessBudgetBurn", "severity": severity, "long_window": long_w,
                           "short_window": short_w, "burn_rate": bl, "threshold": threshold})
    for long_w, short_w, threshold, severity in BURN_ALERTS:
        def incomplete(w):
            return None if windows[w]["complete_ratio"] is None else 1 - windows[w]["complete_ratio"]
        bl, bs = _burn(incomplete(long_w), s.slo_complete_target), _burn(incomplete(short_w), s.slo_complete_target)
        if bl is not None and bs is not None and bl >= threshold and bs >= threshold:
            alerts.append({"alert": "ReportCompletenessBudgetBurn", "severity": severity, "long_window": long_w,
                           "short_window": short_w, "burn_rate": bl, "threshold": threshold})
    lat_budget = 1 - s.slo_latency_quantile
    for long_w, short_w, threshold, severity in BURN_ALERTS:
        bl = None if windows[long_w]["slow_ratio"] is None else windows[long_w]["slow_ratio"] / lat_budget
        bs = None if windows[short_w]["slow_ratio"] is None else windows[short_w]["slow_ratio"] / lat_budget
        if bl is not None and bs is not None and bl >= threshold and bs >= threshold:
            alerts.append({"alert": "RunLatencyBudgetBurn", "severity": severity, "long_window": long_w,
                           "short_window": short_w, "burn_rate": round(bl, 2), "threshold": threshold})
    return {
        "objectives": {"success_ratio": s.slo_success_target, "complete_ratio": s.slo_complete_target,
                       f"latency_p{int(s.slo_latency_quantile * 100)}_s": s.slo_latency_target_s},
        "windows": {k: windows[k] for k in ("5m", "1h", "6h", "1d", "30d")},
        "error_budget": {"period": "30d", "used_fraction": round(used, 3), "remaining_fraction": round(1 - used, 3)},
        "burn_alerts": alerts + await symptom_alerts(),
    }
