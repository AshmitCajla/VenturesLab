"""Chaos experiments: inject a failure, then check the system degrades, alerts and diagnoses itself.

For each experiment the harness turns on one fault, pushes runs through the real
runner (same code path as the API), and records:

* **degradation**: did runs still deliver a report, and how many sections were
  marked unavailable instead of the run failing?
* **alerting**: did an SLO burn-rate alert fire (computed by services/slo.py, the
  same rules Prometheus evaluates)?
* **diagnosis**: does the evidence the ops agent gathers point at the injected
  cause? Offline, the top error signature's category is compared with the
  expected one (deterministic). With ``--live`` the ops agent's own diagnosis
  (reasoning model over MCP tools) is compared too.

    cd backend && python -m evaluation.chaos                # fake models, no keys (CI)
    cd backend && python -m evaluation.chaos --live --runs 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


@dataclass
class Experiment:
    id: str
    fault: str | None
    expected: set[str]                       # acceptable diagnosis categories
    probability: float = 1.0
    params: dict = field(default_factory=dict)
    concurrent: int = 0                      # >0: burst of simultaneous runs
    with_deck: bool = False


EXPERIMENTS = [
    Experiment("baseline", None, {"unknown"}),
    Experiment("llm_timeouts", "llm_timeout", {"llm_timeout"}, probability=0.6),
    Experiment("primary_provider_down", "provider_down", {"llm_provider_outage"}, params={"provider": "primary"}),
    Experiment("web_search_down", "search_down", {"web_search"}),
    Experiment("malformed_decks", "deck_corrupt", {"deck_parsing"}, with_deck=True),
    Experiment("request_burst", None, {"unknown", "capacity"}, concurrent=8),
]

IDEA = "ClinicFlow: WhatsApp-first scheduling assistant that cuts no-shows for independent dental clinics."


def _deck() -> bytes:
    import fitz

    doc = fitz.open()
    for text in ("ClinicFlow\nWhatsApp scheduling for clinics", "Problem\n15% of appointments are no-shows",
                 "Traction\n14 paying clinics, $2.1K MRR"):
        doc.new_page().insert_text((72, 90), text, fontsize=14)
    return doc.tobytes()


async def run_experiment(exp: Experiment, n_runs: int, live: bool) -> dict:
    from core import chaos
    from ops import agent, tools
    from services import slo
    from services.db import get_db
    from services.runs import Run
    from workflow.runner import execute

    await get_db().execute("delete from runs where source = 'chaos'")  # each experiment starts clean
    await get_db().execute("delete from cache")  # a warm cache would hide a search outage
    chaos.clear()
    if exp.fault:
        chaos.activate(exp.fault, exp.probability, 0, **exp.params)
    started = time.time()
    n = exp.concurrent or n_runs
    deck = _deck() if exp.with_deck else None
    batch = [Run(id=str(uuid.uuid4()), source="chaos", input={"idea": IDEA}) for _ in range(n)]
    try:
        if exp.concurrent:
            done = await asyncio.gather(*(execute(r, deck) for r in batch))
        else:
            done = [await execute(r, deck) for r in batch]
    finally:
        chaos.clear()
    elapsed = time.time() - started

    statuses = [r.status for r in done]
    unavailable = [len((r.result or {}).get("degraded_agents", [])) for r in done if r.status == "done"]
    warnings = sum(len(r.warnings) for r in done) + sum(
        len(t.get("warnings", [])) for r in done for t in (r.result or {}).get("trace", []))
    health = await slo.status()
    sigs = await tools.error_signatures(hours=1)
    top3 = [s["category"] for s in sigs[:3]]
    top = top3[0] if top3 else "unknown"
    row = {
        "experiment": exp.id, "fault": exp.fault or "—", "runs": n,
        "delivered": sum(s == "done" for s in statuses), "failed": statuses.count("failed"),
        "sections_unavailable_avg": round(sum(unavailable) / len(unavailable), 1) if unavailable else None,
        "warnings": warnings, "wall_s": round(elapsed, 1),
        "alerts": sorted({a["alert"] for a in health["burn_alerts"]}),
        "top_signal": top, "top_signals": top3,
        # on free tiers rate limits are real background noise, so the injected cause must be in the top 3
        "signal_ok": (not exp.fault and top in exp.expected | {"rate_limit", "unknown"}) or bool(set(top3) & exp.expected),
        "models": sorted({m for r in done for t in (r.result or {}).get("trace", []) for m in t.get("models", [])}),
    }
    if live and exp.fault:
        # The alert must not give the answer away: only what Alertmanager would really send.
        fired = next((a for a in health["burn_alerts"]), None)
        alert = {"labels": {"alertname": fired["alert"] if fired else "ElevatedErrors",
                            "severity": fired["severity"] if fired else "ticket"},
                 "annotations": {"summary": "Investigate the analysis service: an alert fired or users reported problems."}}
        try:
            diag, _ = await agent.diagnose(alert)
            row.update(diagnosis=diag.category, diagnosis_ok=diag.category in exp.expected,
                       diagnosis_cause=diag.likely_cause)
        except Exception as exc:
            row.update(diagnosis=f"failed: {exc}", diagnosis_ok=False)
    return row


def to_markdown(rows: list[dict], live: bool) -> str:
    lines = ["# Chaos experiment report", "",
             f"Mode: {'live models' if live else 'fake models (plumbing and deterministic signals only)'}", "",
             "| experiment | fault | runs | delivered | failed | sections unavailable (avg) | degraded inputs "
             "| SLO alerts fired | top error signals | injected cause among them |"
             + (" ops agent diagnosis | diagnosis correct |" if live else ""),
             "|---|---|---|---|---|---|---|---|---|---|" + ("---|---|" if live else "")]
    for r in rows:
        line = (f"| {r['experiment']} | {r['fault']} | {r['runs']} | {r['delivered']} | {r['failed']} "
                f"| {r['sections_unavailable_avg']} | {r['warnings']} | {', '.join(r['alerts']) or '—'} "
                f"| {', '.join(r.get('top_signals') or [r['top_signal']])} "
                f"| {'✓' if r['signal_ok'] else '✗'} |")
        if live:
            line += f" {r.get('diagnosis', '—')} | {'✓' if r.get('diagnosis_ok') else ('—' if r['fault'] == '—' else '✗')} |"
        lines.append(line)
    return "\n".join(lines) + "\n"


async def main_async(args) -> list[dict]:
    rows = []
    for exp in EXPERIMENTS:
        if args.only and exp.id not in args.only:
            continue
        row = await run_experiment(exp, args.runs, args.live)
        print(json.dumps(row), file=sys.stderr)
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--runs", type=int, default=3, help="runs per experiment (bursts use their own count)")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default=str(HERE / "chaos_report.md"))
    args = ap.parse_args()
    os.environ["CHAOS"] = "1"
    if args.live:
        from core import telemetry

        telemetry.setup("ventureslab-chaos")  # the ops agent reads the JSON log file
    if not args.live:
        os.environ.setdefault("EMBEDDINGS", "hash")
        os.environ.setdefault("WEB_SEARCH", "1")   # search must be "on" for search_down to bite
        os.environ.setdefault("DB_PATH", ":memory:")
        os.environ.setdefault("LOG_DIR", "")
        from core import llm, model_router
        from services import web_research
        from tests.fakes import FakeLLM

        llm.RETRY_DELAY_S = 0
        fake = FakeLLM()
        model_router.set_llm_factory(lambda role: fake)
        # no network in CI; the chaos hook still runs before this stub
        web_research._search_sync = lambda q, n: [{"title": "Report", "body": "Market", "href": "https://example.com"}]
    from core.config import get_settings

    get_settings.cache_clear()
    rows = asyncio.run(main_async(args))
    md = to_markdown(rows, args.live)
    Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
