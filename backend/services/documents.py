"""Turning user inputs (PDF decks, websites) into clean text chunks."""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

MAX_URL_CHARS = 12_000


@dataclass
class Chunk:
    text: str
    page: int | None = None
    source: str = "deck"


# ---------------------------------------------------------------- PDF decks
def extract_pdf_pages(pdf_bytes: bytes) -> list[str]:
    import fitz  # PyMuPDF

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return [page.get_text("text") for page in doc]


def chunk_text(text: str, max_words: int = 180, overlap: int = 40) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, max_words - overlap)
    return [" ".join(words[i : i + max_words]) for i in range(0, len(words), step)
            if words[i : i + max_words]]


def chunk_pages(pages: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_no, page_text in enumerate(pages, start=1):
        cleaned = re.sub(r"\s+", " ", page_text).strip()
        # Slides are short: keep a whole slide together when possible.
        for piece in chunk_text(cleaned):
            chunks.append(Chunk(text=piece, page=page_no))
    return chunks


# ------------------------------------------------------------- web scraping
class UnsafeURLError(ValueError):
    pass


def assert_public_url(url: str) -> None:
    """Block SSRF: only http(s) to public IP addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURLError("Only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Cannot resolve host {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeURLError("URL resolves to a non-public address")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True))


async def scrape_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    await asyncio.to_thread(assert_public_url, url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0,
                                 headers={"User-Agent": "VenturesLab/2.0"}) as client:
        res = await client.get(url)
        res.raise_for_status()
    return html_to_text(res.text)[:MAX_URL_CHARS]
