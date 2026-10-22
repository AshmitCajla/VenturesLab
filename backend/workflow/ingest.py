"""Turns raw inputs (deck PDF, website text) into graph state.

Used by the API and by the red-team harness, so both see exactly the same
defences. With INJECTION_DEFENSES=0 the raw extracted text is passed through,
which is the "before" condition when measuring attack success rate.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from services.documents import chunk_pages, extract_pdf_pages
from services.retrieval import VectorStore
from services.security import Finding, defenses_enabled, extract_visible_pages, quarantine


@dataclass
class Ingested:
    state: dict = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)


def _clean_deck(pdf_bytes: bytes) -> tuple[list[str], list[Finding]]:
    if not defenses_enabled():
        return extract_pdf_pages(pdf_bytes), []
    pages, findings = extract_visible_pages(pdf_bytes)
    clean = []
    for n, text in enumerate(pages, start=1):
        kept, found = quarantine(text, f"slide {n}")
        clean.append(kept)
        findings.extend(found)
    return clean, findings


async def ingest(session_id: str, store: VectorStore, pdf_bytes: bytes | None = None,
                 url_content: str | None = None) -> Ingested:
    out = Ingested()
    if pdf_bytes:
        pages, found = await asyncio.to_thread(_clean_deck, pdf_bytes)
        out.findings.extend(found)
        out.state["deck_summary"] = "\n".join(pages[:3])[:4000]
        n = await store.index(session_id, chunk_pages(pages))
        out.state["deck_indexed"] = n > 0
        out.progress.append(f"Indexed pitch deck ({len(pages)} pages, {n} chunks)")
    if url_content:
        if defenses_enabled():
            url_content, found = quarantine(url_content, "website")
            out.findings.extend(found)
        out.state["url_content"] = url_content
    if out.findings:
        out.progress.append(f"Removed {len(out.findings)} suspicious passage(s) from the inputs")
    return out
