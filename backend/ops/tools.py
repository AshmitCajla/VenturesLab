"""Observability tools for the ops agent (exposed over MCP by mcp_servers/ops_server.py).

They read the same sources an on-call engineer would: the run store, the JSON
log file, Prometheus, Jaeger and the runbooks. Prometheus and Jaeger are
optional; a tool that cannot reach them says so instead of failing, so the
agent still works on a free-tier deploy with only the database and logs.

No tool reveals chaos-test state: the agent must diagnose from symptoms.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

from core.config import get_settings
from services import slo
from services.db import get_db


def runbook_dir() -> Path:
    return Path(get_settings().runbook_dir or Path(__file__).resolve().parents[2] / "docs" / "runbooks")


# error signature -> likely cause category (hints for the agent, not conclusions)
SIGNALS = [
    (r"interrupted by a server restart", "restart"),
    (r"rate.?limit|\b429\b|tokens per (minute|day)|TPM|TPD|RPM", "rate_limit"),
    (r"timeout|timed out|ReadTimeout|deadline", "llm_timeout"),
    (
        r"provider .* is down|Connection(Error| refused)|APIConnectionError|\b50[234]\b|InternalServerError|"
        r"NotFound|\b404\b|model_not_found|decommissioned",
        "llm_provider_outage",
    ),
    (r"web search|ddgs|search unavailable|DNSError", "web_search"),
    (r"pdf|deck (could not be|skipped)|could not be (read|parsed)|DeckUnreadable|fitz|mupdf", "deck_parsing"),
    (r"json|parse|validation error|tool_use_failed|Failed to generate", "model_output_format"),
    (r"budget", "cost_budget"),
]


def classify(message: str) -> str:
    for pattern, category in SIGNALS:
        if re.search(pattern, message or "", re.IGNORECASE):
            return category
    return "unknown"


def _signature(message: str) -> str:
    """Collapse ids, numbers and quoted values so similar errors group together."""
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", message or "")
    s = re.sub(r"'[^']{20,}'", "'…'", s)
    s = re.sub(r"\d+(\.\d+)?", "N", s)
    return s[:180]


async def _runs(hours: float, status: str | None = None, limit: int = 500) -> list[dict]:
    since = time.time() - hours * 3600
    sql = (
        "select id, status, source, title, created, finished, error, cost_usd, progress, warnings from runs "
        "where created >= ?" + (" and status = ?" if status else "") + " order by created desc limit ?"
    )
    rows = await get_db().fetchall(sql, (since, status, limit) if status else (since, limit))
    for r in rows:
        r["progress"] = json.loads(r["progress"] or "[]")
        r["warnings"] = json.loads(r["warnings"] or "[]")
    return rows


async def slo_status() -> dict:
    """Current SLO compliance, error-budget use and any burn-rate alerts (computed from the run store)."""
    return await slo.status()


async def recent_runs(hours: float = 24, status: str | None = None, limit: int = 20) -> list[dict]:
    """Recent runs with status, duration, cost, error and which agents failed."""
    out = []
    for r in (await _runs(hours, status))[:limit]:
        failed = [p["name"] for p in r["progress"] if p.get("status") == "error"]
        out.append(
            {
                "run_id": r["id"],
                "status": r["status"],
                "source": r["source"],
                "title": r["title"],
                "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created"])),
                "duration_s": round(r["finished"] - r["created"], 1) if r["finished"] else None,
                "cost_usd": r["cost_usd"],
                "error": r["error"],
                "failed_agents": failed,
            }
        )
    return out


async def error_signatures(hours: float = 1) -> list[dict]:
    """Agent errors, run failures, degraded-input warnings and errors absorbed by retries/fallbacks,
    grouped by signature, with counts, likely category and an example."""
    groups: dict[str, dict] = {}
    for r in await _runs(hours):
        msgs = [
            (p.get("name", "?"), p["error"]) for p in r["progress"] if p.get("status") == "error" and p.get("error")
        ]
        # degraded inputs (search down, deck unreadable) do not fail anything, so they are warnings
        msgs += [(p.get("name", "?"), w) for p in r["progress"] for w in p.get("warnings", [])]
        msgs += [("run", w) for w in r["warnings"]]
        # errors absorbed by retries and fallbacks: the run looks healthy, the provider is not
        msgs += [(p.get("name", "?"), e) for p in r["progress"] for e in p.get("recovered_errors", [])]
        if r["status"] == "failed" and r["error"]:
            msgs.append(("run", r["error"]))
        for agent, msg in msgs:
            sig = _signature(msg)
            g = groups.setdefault(
                sig,
                {
                    "signature": sig,
                    "count": 0,
                    "agents": Counter(),
                    "category": classify(msg),
                    "example": msg[:400],
                    "runs": set(),
                },
            )
            g["count"] += 1
            g["agents"][agent] += 1
            g["runs"].add(r["id"])
    out = sorted(groups.values(), key=lambda g: -g["count"])
    return [{**g, "agents": dict(g["agents"].most_common(5)), "runs": len(g["runs"])} for g in out[:10]]


async def agent_health(hours: float = 24) -> list[dict]:
    """Per agent: runs, errors, skips, median and p95 latency."""
    stats: dict[str, dict] = defaultdict(lambda: {"ok": 0, "error": 0, "skipped": 0, "ms": []})
    for r in await _runs(hours):
        for p in r["progress"]:
            if "number" not in p:
                continue
            s = stats[p["name"]]
            s[p.get("status", "ok")] = s.get(p.get("status", "ok"), 0) + 1
            if p.get("status") == "ok" and p.get("ms"):
                s["ms"].append(p["ms"])
    out = []
    for name, s in stats.items():
        ms = sorted(s.pop("ms"))
        out.append(
            {
                "agent": name,
                **s,
                "median_ms": round(statistics.median(ms)) if ms else None,
                "p95_ms": ms[min(len(ms) - 1, int(0.95 * len(ms)))] if ms else None,
            }
        )
    return sorted(out, key=lambda x: (-x["error"], -(x["p95_ms"] or 0)))


async def recent_logs(
    level: str = "WARNING", contains: str | None = None, run_id: str | None = None, minutes: float = 60, limit: int = 40
) -> list[dict]:
    """Structured log lines (newest last) at or above a level, optionally filtered by text or run id."""
    log_dir = get_settings().log_dir
    path = Path(log_dir) / "ventureslab.jsonl" if log_dir else None
    if not path or not path.exists():
        return [{"note": "no log file configured (LOG_DIR)"}]
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    floor, since = order.get(level.upper(), 30), time.time() - minutes * 60
    lines = path.read_text(errors="replace").splitlines()[-20000:]
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("ts", 0) < since or order.get(rec.get("level"), 0) < floor:
            continue
        if run_id and rec.get("run_id") != run_id:
            continue
        if contains and contains.lower() not in rec.get("msg", "").lower():
            continue
        rec.pop("exc", None)
        rec["msg"] = rec.get("msg", "")[:300]
        out.append(rec)
    return out[-limit:]


async def query_prometheus(promql: str) -> dict:
    """Run an instant PromQL query, e.g. sum by (provider) (rate(ventureslab_llm_calls_total{outcome="error"}[5m]))."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{get_settings().prometheus_url}/api/v1/query", params={"query": promql})
        data = r.json().get("data", {}).get("result", [])
        return {"result": [{"labels": d["metric"], "value": d["value"][1]} for d in data][:30]}
    except Exception as exc:
        return {"unavailable": f"Prometheus not reachable ({type(exc).__name__})"}


async def get_trace(run_id: str) -> dict:
    """The run's OpenTelemetry trace from Jaeger: slowest spans and spans with errors."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{get_settings().jaeger_url}/api/traces",
                params={"service": "ventureslab-api", "limit": 1, "tags": json.dumps({"ventureslab.run_id": run_id})},
            )
        traces = r.json().get("data", [])
    except Exception as exc:
        return {"unavailable": f"Jaeger not reachable ({type(exc).__name__})"}
    if not traces:
        return {"note": "no trace found for this run"}
    spans = traces[0]["spans"]

    def tags(s):
        return {t["key"]: t["value"] for t in s.get("tags", [])}

    slow = sorted(spans, key=lambda s: -s["duration"])[:8]
    errs = [s for s in spans if "error" in tags(s)][:8]

    def fmt(s):
        return {
            "span": s["operationName"],
            "ms": round(s["duration"] / 1000),
            **{k: v for k, v in tags(s).items() if k in ("agent", "llm.model", "llm.provider", "error")},
        }

    return {
        "trace_id": traces[0]["traceID"],
        "spans": len(spans),
        "slowest": [fmt(s) for s in slow],
        "errors": [fmt(s) for s in errs],
    }


async def model_health() -> list[dict]:
    """Latest fallback-canary result for every model in every provider chain (up/down, latency, error)."""
    from ops import canary

    return canary.last_results() or [{"note": "no canary results yet (CANARY_INTERVAL_S)"}]


async def list_runbooks() -> list[str]:
    """Names of the available runbooks."""
    return sorted(p.stem for p in runbook_dir().glob("*.md"))


async def read_runbook(name: str) -> str:
    """Full text of a runbook, e.g. 'llm-provider-errors'."""
    path = runbook_dir() / f"{Path(name).stem}.md"
    return path.read_text() if path.exists() else f"no runbook named {name!r}; see list_runbooks"


async def incident_history(limit: int = 10) -> list[dict]:
    """Recent incidents with their diagnosis."""
    rows = await get_db().fetchall(
        "select id, alert, status, started, resolved, diagnosis from incidents order by started desc limit ?", (limit,)
    )
    for r in rows:
        r["diagnosis"] = json.loads(r["diagnosis"]) if r["diagnosis"] else None
    return rows
