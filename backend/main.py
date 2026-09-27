"""VenturesLab API.

A full 12-agent run takes minutes, which is longer than most proxies and free
hosts allow for a single HTTP request. So analysis is a background *job*:

    POST /analyze          -> 202 {"job_id": ...}
    GET  /jobs/{job_id}    -> status, per-agent progress, result when done
    GET  /agents           -> the 12 agents and their order
    GET  /health
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.model_router import available_providers
from services.documents import UnsafeURLError, chunk_pages, extract_pdf_pages, scrape_url
from services.retrieval import get_store
from services.storage import save_analysis
from workflow.graph import AGENTS, compiled_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

settings = get_settings()
app = FastAPI(title="VenturesLab API", version="2.0.0",
              description="12-agent venture analysis with RAG, evaluation checks and self-correction.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","),
                   allow_methods=["*"], allow_headers=["*"])


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | failed
    created: float = field(default_factory=time.time)
    finished: float | None = None
    progress: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "job_id": self.id, "status": self.status, "progress": self.progress,
            "warnings": self.warnings, "error": self.error, "result": self.result,
            "elapsed_s": round((self.finished or time.time()) - self.created, 1),
        }


JOBS: dict[str, Job] = {}
MAX_JOBS_KEPT = 200
_slots = asyncio.Semaphore(settings.max_concurrent_jobs)


def _summarise_evals(evals: list[dict]) -> dict:
    total = len(evals)
    passed = sum(e["passed"] for e in evals)
    return {"passed": passed, "total": total,
            "pass_rate": round(passed / total, 3) if total else None,
            "failed": [e for e in evals if not e["passed"]]}


async def run_job(job: Job, idea: str, url: str, pdf_bytes: bytes | None) -> None:
    async with _slots:
        job.status = "running"
        store = get_store()
        state: dict[str, Any] = {"session_id": job.id, "idea": idea, "trace": [], "evaluations": []}
        try:
            if pdf_bytes:
                pages = await asyncio.to_thread(extract_pdf_pages, pdf_bytes)
                state["deck_summary"] = "\n".join(pages[:3])[:4000]
                n = await store.index(job.id, chunk_pages(pages))
                state["deck_indexed"] = n > 0
                job.progress.append({"node": "ingest", "name": f"Indexed pitch deck ({len(pages)} pages, {n} chunks)",
                                     "status": "ok"})
            if url:
                try:
                    state["url_content"] = await scrape_url(url)
                    job.progress.append({"node": "scrape", "name": "Read website", "status": "ok"})
                except (UnsafeURLError, Exception) as exc:
                    job.warnings.append(f"Could not read {url}: {exc}")

            final = dict(state)
            async for update in compiled_graph.astream(state, stream_mode="updates"):
                for node, delta in update.items():
                    if not delta:
                        continue
                    for key, val in delta.items():
                        if key in ("trace", "evaluations"):
                            final[key] = final.get(key, []) + val
                        else:
                            final[key] = val
                    for t in delta.get("trace", []):
                        job.progress.append({"node": node, "name": t["agent"], **t})

            final["evaluation_summary"] = _summarise_evals(final.get("evaluations", []))
            final.pop("deck_summary", None)
            final["storage"] = await save_analysis(job.id, final)
            job.result, job.status = final, "done"
        except Exception as exc:
            log.exception("job %s failed", job.id)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = time.time()
            await store.drop(job.id)


def _evict_old_jobs() -> None:
    if len(JOBS) <= MAX_JOBS_KEPT:
        return
    for jid in sorted(JOBS, key=lambda j: JOBS[j].created)[: len(JOBS) - MAX_JOBS_KEPT]:
        if JOBS[jid].status in ("done", "failed"):
            JOBS.pop(jid, None)


@app.get("/health")
async def health():
    return {"status": "ok", "llm_providers": available_providers(),
            "vector_store": type(get_store()).__name__, "jobs": len(JOBS)}


@app.get("/agents")
async def agents():
    return AGENTS


@app.post("/analyze", status_code=202)
async def analyze(idea: str = Form(""), url: str = Form(""), file: UploadFile | None = File(None)):
    idea, url = idea.strip()[:8000], url.strip()
    pdf_bytes = None
    if file is not None and file.filename:
        pdf_bytes = await file.read()
        if len(pdf_bytes) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"PDF larger than {settings.max_upload_mb} MB")
        if not pdf_bytes.startswith(b"%PDF"):
            raise HTTPException(415, "Only PDF pitch decks are supported")
    if not (idea or url or pdf_bytes):
        raise HTTPException(422, "Provide at least one of: idea, url, file")

    job = Job(id=str(uuid.uuid4()))
    JOBS[job.id] = job
    _evict_old_jobs()
    asyncio.create_task(run_job(job, idea, url, pdf_bytes))
    return {"job_id": job.id, "status": job.status, "poll": f"/jobs/{job.id}"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job id")
    return job.public()
