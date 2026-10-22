"""Executes one analysis run end to end. Shared by the API, Slack, MCP and tests.

    queue (slot semaphore) -> scrape website -> ingest (defences, vision, RAG index)
      -> agent graph (streamed: each finished agent is a progress event)
      -> finalise: quality summary, confidence, memory, cost -> persist

``on_update`` is awaited after every progress event, which is how Slack edits
its live progress message. One run id ties together the run row, every log
line, the OTel trace, the Prometheus exemplars and the Slack thread.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from core import runctx
from core.config import get_settings
from core.telemetry import RUN_COST, RUN_DURATION, RUNS, RUNS_ACTIVE, RUNS_DEGRADED, RUNS_QUEUED, current_trace_id, span
from services import memory
from services.documents import UnsafeURLError, scrape_url
from services.retrieval import get_store
from services.runs import Run, runs
from services.storage import save_analysis
from workflow.confidence import report_confidence
from workflow.graph import compiled_graph
from workflow.ingest import ingest

log = logging.getLogger("runner")

OnUpdate = Callable[[Run], Awaitable[None]] | None
_slots: dict[int, asyncio.Semaphore] = {}


def slots() -> asyncio.Semaphore:
    """One concurrency limiter per event loop (a semaphore cannot be shared across loops)."""
    loop = id(asyncio.get_running_loop())
    if loop not in _slots:
        _slots[loop] = asyncio.Semaphore(get_settings().max_concurrent_jobs)
    return _slots[loop]


def summarise_evals(evals: list[dict]) -> dict:
    total = len(evals)
    passed = sum(e["passed"] for e in evals)
    return {"passed": passed, "total": total, "pass_rate": round(passed / total, 3) if total else None,
            "failed": [e for e in evals if not e["passed"]]}


async def _notify(run: Run, on_update: OnUpdate) -> None:
    await runs.save(run)
    if on_update:
        try:
            await on_update(run)
        except Exception as exc:  # a Slack hiccup must not fail the analysis
            log.warning("progress callback failed: %s", exc)


async def execute(run: Run, pdf_bytes: bytes | None = None, on_update: OnUpdate = None,
                  budget_usd: float | None = None) -> Run:
    idea, url = run.input.get("idea", ""), run.input.get("url", "")
    RUNS_QUEUED.inc()
    await runs.save(run)
    async with slots():
        RUNS_QUEUED.dec()
        RUNS_ACTIVE.inc()
        ctx = runctx.start(run.id, get_settings().run_budget_usd if budget_usd is None else budget_usd)
        run.status, run.started = "running", time.time()
        store = get_store()
        try:
            with span("run", **{"run.source": run.source}) as s:
                run.input["trace_id"] = current_trace_id()
                await _notify(run, on_update)
                result = await _analyse(run, idea, url, pdf_bytes, store, on_update)
                result["cost"] = ctx.summary()
                result["trace_id"] = run.input.get("trace_id")
                result["storage"] = await save_analysis(run.id, result)
                run.result, run.status = result, "done"
                run.score, run.recommendation = result.get("score"), result.get("recommendation")
                run.title = (result.get("venture_profile") or {}).get("name") or run.title
                s.set_attribute("run.recommendation", run.recommendation or "")
        except Exception as exc:
            log.exception("run %s failed", run.id)
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
        finally:
            run.finished = time.time()
            run.cost_usd = round(ctx.spent_usd, 6)
            RUNS_ACTIVE.dec()
            RUNS.labels(run.status, run.source).inc()
            if run.status == "done" and (run.result or {}).get("degraded_agents"):
                RUNS_DEGRADED.labels(run.source).inc()
            RUN_DURATION.labels(run.source).observe(run.finished - run.created)
            RUN_COST.observe(run.cost_usd)
            await store.drop(run.id)
            await _notify(run, on_update)
    return run


async def _analyse(run: Run, idea: str, url: str, pdf_bytes: bytes | None, store, on_update: OnUpdate) -> dict:
    url_content = None
    if url:
        try:
            url_content = await scrape_url(url)
            run.progress.append({"node": "scrape", "name": "Read website", "status": "ok"})
        except (UnsafeURLError, Exception) as exc:
            run.warnings.append(f"Could not read {url}: {exc}")
    ingested = await ingest(run.id, store, pdf_bytes, url_content)
    run.warnings.extend(ingested.warnings)
    for msg in ingested.progress:
        run.progress.append({"node": "ingest", "name": msg, "status": "ok"})
    if not (idea or ingested.state.get("deck_indexed") or ingested.state.get("url_content")):
        raise ValueError("nothing to analyse: the deck could not be read and no description or website was usable")
    await _notify(run, on_update)

    state: dict[str, Any] = {"session_id": run.id, "idea": idea, "trace": [], "evaluations": [],
                             "evidence": {}, **ingested.state}
    final = dict(state)
    async for update in compiled_graph.astream(state, stream_mode="updates"):
        for node, delta in update.items():
            if not delta:
                continue
            for key, val in delta.items():
                if key in ("trace", "evaluations"):
                    final[key] = final.get(key, []) + val
                elif key == "evidence":
                    final[key] = {**final.get(key, {}), **val}
                else:
                    final[key] = val
            if node == "plan":
                planned = [k for k, v in delta["plan"]["agents"].items() if v["run"]]
                run.progress.append({"node": "plan", "name": f"Plan: optional agents {', '.join(planned) or 'none'}",
                                     "status": "ok"})
            for t in delta.get("trace", []):
                run.progress.append({"node": node, "name": t["agent"], **t})
            if node == "understand" and isinstance(delta.get("venture_profile"), dict):
                run.title = delta["venture_profile"].get("name") or run.title
            await _notify(run, on_update)

    final["evaluation_summary"] = summarise_evals(final.get("evaluations", []))
    # graceful degradation: the report is delivered, with these sections marked unavailable
    final["degraded_agents"] = [t["agent"] for t in final.get("trace", []) if t["status"] == "error"]
    final["confidence"] = report_confidence(final)
    try:
        final["history"] = await memory.compare_with_history(run.id, final)
        await memory.remember(run.id, final)
    except Exception as exc:  # memory is a nice-to-have
        log.warning("memory unavailable: %s", exc)
        final["history"] = {}
    for k in ("deck_summary", "session_id"):  # deck_pages stay: follow-up answers quote slides
        final.pop(k, None)
    return final
