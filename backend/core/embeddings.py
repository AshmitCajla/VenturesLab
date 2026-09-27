"""Text embeddings for pitch-deck retrieval.

Default: ``fastembed`` running BAAI/bge-small-en-v1.5 on ONNX Runtime
(~130 MB RAM, CPU only). This replaces the original torch + CLIP stack, which
pulled in >1.5 GB of dependencies and could not run on free hosting tiers.

``EMBEDDINGS=hash`` switches to a dependency-free hashing embedder that is
deterministic and good enough for tests and very small deployments.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from core.config import get_settings


class Embedder(Protocol):
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Bag-of-words feature hashing with L2 normalisation."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class FastEmbedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts))]


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embeddings_backend == "hash":
        return HashingEmbedder()
    try:
        return FastEmbedder(s.embedding_model)
    except Exception as exc:  # model download blocked, missing wheel, ...
        print(f"[embeddings] fastembed unavailable ({exc}); using hashing embedder")
        return HashingEmbedder()
