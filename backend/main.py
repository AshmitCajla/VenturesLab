"""VenturesLab API.

A full run takes minutes, which is longer than most proxies and free hosts allow
for a single HTTP request. So analysis is a background *run*:

    POST /analyze                 -> 202 {"run_id": ...}
    GET  /runs/{id}               -> status, per-agent progress, result when done
    GET  /runs                    -> history (filter by user)
    POST /runs/{id}/ask           -> follow-up question answered from that run
    POST /runs/{id}/feedback      -> thumbs up/down or a correction (feeds the eval set)
    GET  /runs/{id}/report.md     -> Markdown summary
    GET  /agents  /health  /metrics  /slo
    POST /ops/alerts              -> Alertmanager webhook -> ops agent incident
    /mcp                          -> VenturesLab MCP server (streamable HTTP)
    GET|POST|DELETE /chaos        -> fault injection (only with CHAOS=1)

``GET /jobs/{id}`` is kept as an alias for v2 clients. With ``API_KEYS`` set, every
route except /health, /metrics and the webhook needs a key (core/auth.py).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from core import chaos, telemetry
from core.auth import AuthMiddleware
from core.config import get_settings
from core.model_router import available_providers
from core.telemetry import FEEDBACK
from services import qa, slo
from services.report_text import to_markdown
from services.retrieval import get_store
from services.runs import Run, runs
from workflow.graph import AGENTS
from workflow.runner import execute

settings = get_settings()
_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    telemetry.setup()
    await runs.mark_interrupted()
    from ops import canary
    from slack_app import bolt

    background = [
        t
        for t in (
            await bolt.start(),  # Slack bot + weekly digest, when Slack tokens are set
            asyncio.create_task(canary.loop()),
        )
        if t
    ]
    from mcp_servers.ventureslab_server import http_mount

    async with http_mount.running():  # VenturesLab MCP server at /mcp, behind the same API keys
        yield
    for t in background:
        t.cancel()


app = FastAPI(
    title="VenturesLab API",
    version="3.0.0",
    lifespan=lifespan,
    description="Multi-agent venture analysis with evidence checks, injection defences and SRE tooling.",
)
app.add_middleware(AuthMiddleware)  # added before CORS so CORS wraps it and 401s carry CORS headers
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_methods=["*"], allow_headers=["*"]
)


def start_run(run: Run, pdf_bytes: bytes | None = None, on_update=None) -> None:
    task = asyncio.create_task(execute(run, pdf_bytes, on_update))
    _tasks.add(task)  # keep a reference so the task is not garbage-collected
    task.add_done_callback(_tasks.discard)


def validate_pdf(pdf_bytes: bytes) -> None:
    if len(pdf_bytes) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"PDF larger than {settings.max_upload_mb} MB")
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(415, "Only PDF pitch decks are supported")


async def _get(run_id: str) -> Run:
    run = await runs.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run id")
    return run


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "llm_providers": available_providers(),
        "vector_store": type(get_store()).__name__,
        "active_runs": len(runs.active),
        "chaos": chaos.active() if chaos.enabled() else None,
    }


@app.get("/agents")
async def agents():
    return AGENTS


@app.post("/analyze", status_code=202)
async def analyze(
    request: Request,
    idea: str = Form(""),
    url: str = Form(""),
    user: str = Form(""),
    file: UploadFile | None = File(None),
):
    idea, url = idea.strip()[:8000], url.strip()
    pdf_bytes = None
    if file is not None and file.filename:
        pdf_bytes = await file.read()
        validate_pdf(pdf_bytes)
    if not (idea or url or pdf_bytes):
        raise HTTPException(422, "Provide at least one of: idea, url, file")
    run = Run(
        id=str(uuid.uuid4()),
        source="api",
        user_id=user or getattr(request.state, "caller", None),
        input={"idea": idea, "url": url, "filename": file.filename if pdf_bytes else None},
    )
    await runs.save(run)
    start_run(run, pdf_bytes)
    return {"job_id": run.id, "run_id": run.id, "status": run.status, "poll": f"/runs/{run.id}"}


@app.get("/runs")
async def list_runs(user: str | None = None, limit: int = 20, status: str | None = None):
    return [r.public(include_result=False) for r in await runs.list(user, min(limit, 200), status=status)]


@app.get("/runs/{run_id}")
@app.get("/jobs/{run_id}")
async def get_run(run_id: str):
    return (await _get(run_id)).public()


@app.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def report_md(run_id: str):
    run = await _get(run_id)
    if run.status != "done":
        raise HTTPException(409, f"run is {run.status}")
    return to_markdown(run.result, run.id)


@app.post("/runs/{run_id}/ask")
async def ask(run_id: str, question: str = Body(..., embed=True), history: list[dict] | None = Body(None, embed=True)):
    run = await _get(run_id)
    if run.status != "done":
        raise HTTPException(409, f"run is {run.status}")
    return (await qa.answer(run.result, question[:2000], history)).model_dump()


@app.post("/runs/{run_id}/feedback")
async def feedback(
    run_id: str,
    rating: str = Body(...),
    user: str | None = Body(None),
    section: str | None = Body(None),
    comment: str | None = Body(None),
):
    if rating not in ("up", "down", "correction"):
        raise HTTPException(422, "rating must be up, down or correction")
    await _get(run_id)
    await runs.add_feedback(run_id, rating, user, section, comment)
    FEEDBACK.labels(rating).inc()
    return {"ok": True}


@app.post("/digest/send")
async def send_digest(days: int = 7):
    from services import digest

    title, blocks = await digest.build(days)
    ts = await digest.send(days)
    return {"title": title, "posted": bool(ts), "blocks": blocks}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health/models")
async def model_health(probe: bool = False):
    """Fallback canary results; ?probe=true runs a probe now."""
    from ops import canary

    return await canary.probe_once() if probe else canary.last_results()


@app.get("/slo")
async def slo_status():
    return await slo.status()


@app.post("/ops/alerts")
async def alerts(payload: dict = Body(...), authorization: str | None = Header(None)):
    if settings.alert_webhook_token and authorization != f"Bearer {settings.alert_webhook_token}":
        raise HTTPException(401, "bad token")
    from ops.incidents import handle_alertmanager

    task = asyncio.create_task(handle_alertmanager(payload))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"accepted": len(payload.get("alerts", []))}


@app.get("/ops/incidents")
async def incidents(limit: int = 20):
    from ops.incidents import list_incidents

    return await list_incidents(limit)


@app.post("/ops/ask")
async def ops_ask(question: str = Body(..., embed=True)):
    from ops.agent import ask_ops

    return (await ask_ops(question[:2000])).model_dump()


def _chaos_guard():
    if not chaos.enabled():
        raise HTTPException(403, "fault injection is disabled (set CHAOS=1)")


@app.get("/chaos")
async def chaos_list():
    _chaos_guard()
    return chaos.active()


@app.post("/chaos")
async def chaos_on(
    name: str = Body(...),
    probability: float = Body(1.0),
    duration_s: float = Body(300),
    params: dict = Body(default_factory=dict),
):
    _chaos_guard()
    try:
        return chaos.activate(name, probability, duration_s, **params).to_dict()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/chaos")
async def chaos_off(name: str | None = None):
    _chaos_guard()
    chaos.clear(name)
    return chaos.active()


def _mount_mcp() -> None:
    """Mounted last, at the root, so it only sees paths no API route matched (it serves /mcp)."""
    from mcp_servers.ventureslab_server import http_mount

    app.mount("/", http_mount)


_mount_mcp()
