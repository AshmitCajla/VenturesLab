"""Per-session retrieval over pitch-deck chunks (the "R" in RAG).

Two interchangeable stores:
* ``InMemoryStore`` - numpy cosine search, zero infrastructure (default)
* ``QdrantStore``   - used automatically when QDRANT_URL is set

Every agent asks the store for the deck passages relevant to *its* question
(e.g. the financial agent asks about revenue, burn and pricing), so a
50-slide deck never has to be stuffed into a single prompt.
"""
from __future__ import annotations

import asyncio
import uuid
from functools import lru_cache
from typing import Protocol

import numpy as np

from core.config import get_settings
from core.embeddings import get_embedder
from services.documents import Chunk


class VectorStore(Protocol):
    async def index(self, session_id: str, chunks: list[Chunk]) -> int: ...
    async def search(self, session_id: str, query: str, k: int = 4) -> list[Chunk]: ...
    async def drop(self, session_id: str) -> None: ...


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[np.ndarray, list[Chunk]]] = {}

    async def index(self, session_id: str, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        vecs = await asyncio.to_thread(get_embedder().embed, [c.text for c in chunks])
        self._data[session_id] = (np.asarray(vecs, dtype=np.float32), chunks)
        return len(chunks)

    async def search(self, session_id: str, query: str, k: int = 4) -> list[Chunk]:
        if session_id not in self._data:
            return []
        matrix, chunks = self._data[session_id]
        q = np.asarray((await asyncio.to_thread(get_embedder().embed, [query]))[0], dtype=np.float32)
        scores = matrix @ q
        top = np.argsort(-scores)[:k]
        return [chunks[i] for i in top]

    async def drop(self, session_id: str) -> None:
        self._data.pop(session_id, None)


class QdrantStore:
    COLLECTION = "pitch_deck_chunks"

    def __init__(self, url: str, api_key: str | None) -> None:
        from qdrant_client import AsyncQdrantClient

        self._client = AsyncQdrantClient(url=url, api_key=api_key or None)
        self._ready = False

    async def _ensure(self) -> None:
        if self._ready:
            return
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        if not await self._client.collection_exists(self.COLLECTION):
            await self._client.create_collection(
                self.COLLECTION,
                vectors_config=VectorParams(size=get_embedder().dim, distance=Distance.COSINE),
            )
            await self._client.create_payload_index(
                self.COLLECTION, "session_id", PayloadSchemaType.KEYWORD
            )
        self._ready = True

    async def index(self, session_id: str, chunks: list[Chunk]) -> int:
        from qdrant_client.models import PointStruct

        if not chunks:
            return 0
        await self._ensure()
        vecs = await asyncio.to_thread(get_embedder().embed, [c.text for c in chunks])
        points = [
            PointStruct(id=str(uuid.uuid4()), vector=v,
                        payload={"session_id": session_id, "text": c.text, "page": c.page})
            for c, v in zip(chunks, vecs, strict=True)
        ]
        await self._client.upsert(self.COLLECTION, points=points)
        return len(points)

    async def search(self, session_id: str, query: str, k: int = 4) -> list[Chunk]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await self._ensure()
        q = (await asyncio.to_thread(get_embedder().embed, [query]))[0]
        res = await self._client.query_points(
            self.COLLECTION, query=q, limit=k,
            query_filter=Filter(must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))]),
        )
        return [Chunk(text=p.payload["text"], page=p.payload.get("page")) for p in res.points]

    async def drop(self, session_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        await self._client.delete(self.COLLECTION, points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="session_id", match=MatchValue(value=session_id))])))


@lru_cache
def get_store() -> VectorStore:
    s = get_settings()
    if s.qdrant_url:
        return QdrantStore(s.qdrant_url, s.qdrant_api_key)
    return InMemoryStore()


def format_context(chunks: list[Chunk]) -> str:
    return "\n".join(f"[slide {c.page}] {c.text}" for c in chunks)
