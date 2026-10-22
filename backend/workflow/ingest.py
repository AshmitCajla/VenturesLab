"""Turns raw inputs (deck PDF, website text) into graph state.

Used by the API, Slack, MCP and the red-team harness, so all of them see
exactly the same defences:

    deck -> visible text only -> + vision transcription of charts/images
         -> instruction quarantine -> semantic screen -> chunk + index

With INJECTION_DEFENSES=0 the raw extracted text is passed through, which is the
"before" condition when measuring attack success rate.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field

from core import chaos
from core.config import get_settings
from core.telemetry import DECK_FAILURES, INJECTIONS, span
from services import vision
from services.documents import Chunk, chunk_pages, extract_pdf_pages
from services.retrieval import VectorStore
from services.security import Finding, defenses_enabled, extract_visible_pages, quarantine, semantic_screen, summarize


class DeckUnreadable(ValueError):
    pass


@dataclass
class Ingested:
    state: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    screen_status: str = "off"

    @property
    def security(self) -> dict:
        return summarize(self.findings, self.screen_status)


def _extract(pdf_bytes: bytes) -> tuple[list[str], list[Finding]]:
    chaos.before_deck_parse()
    try:
        if not defenses_enabled():
            return extract_pdf_pages(pdf_bytes), []
        return extract_visible_pages(pdf_bytes)
    except Exception as exc:
        raise DeckUnreadable(f"the PDF could not be read ({type(exc).__name__}: {exc})") from exc


async def ingest(session_id: str, store: VectorStore, pdf_bytes: bytes | None = None,
                 url_content: str | None = None) -> Ingested:
    out = Ingested()
    docs: dict[str, str] = {}
    n_pages = 0
    with span("ingest", has_deck=bool(pdf_bytes), has_website=bool(url_content)):
        if pdf_bytes:
            try:
                pages, found = await asyncio.to_thread(_extract, pdf_bytes)
            except (DeckUnreadable, chaos.InjectedFault) as exc:
                DECK_FAILURES.inc()
                out.warnings.append(f"Pitch deck skipped: {exc}")
                pages, found = [], []
            out.findings.extend(found)
            n_pages = len(pages)
            visuals = await vision.read_visuals(pdf_bytes) if pages else {}
            for n, text in visuals.items():
                pages[n - 1] = f"{pages[n - 1]}\n[visual content]\n{text}".strip()
            if visuals:
                out.progress.append(f"Read charts and images on {len(visuals)} slide(s)")
            docs.update({f"slide {n}": text for n, text in enumerate(pages, start=1)})
        if url_content:
            docs["website"] = url_content

        if defenses_enabled():
            for loc, text in docs.items():
                docs[loc], found = quarantine(text, loc)
                out.findings.extend(found)
            if get_settings().semantic_screen and any(t.strip() for t in docs.values()):
                docs, found, out.screen_status = await semantic_screen(docs)
                out.findings.extend(found)

        if n_pages:
            pages = [docs.get(f"slide {n}", "") for n in range(1, n_pages + 1)]
            out.state["deck_pages"] = pages
            out.state["deck_hash"] = hashlib.sha256("\n".join(pages).encode()).hexdigest()[:16]
            out.state["deck_summary"] = "\n".join(pages[:3])[:4000]
            chunks: list[Chunk] = chunk_pages(pages)
            n = await store.index(session_id, chunks)
            out.state["deck_indexed"] = n > 0
            out.progress.append(f"Indexed pitch deck ({n_pages} pages, {n} chunks)")
        if "website" in docs:
            out.state["url_content"] = docs["website"]

    for f in out.findings:
        INJECTIONS.labels(f.kind).inc()
    if out.findings:
        out.progress.append(f"Removed {len(out.findings)} suspicious passage(s) from the inputs")
    out.state["security"] = out.security
    return out
