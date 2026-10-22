"""Persistent TTL cache for repeated lookups (web search, market data).

Two runs on similar ventures ask similar questions; a cache hit saves a slow,
flaky network call and keeps runs reproducible within the TTL.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from prometheus_client import Counter

from services.db import get_db

CACHE = Counter("ventureslab_cache_total", "Cache lookups", ["namespace", "result"])


def _key(namespace: str, payload: Any) -> str:
    return namespace + ":" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


async def get(namespace: str, payload: Any) -> Any | None:
    row = await get_db().fetchone("select value, expires from cache where key = ?", (_key(namespace, payload),))
    hit = row is not None and row["expires"] > time.time()
    CACHE.labels(namespace, "hit" if hit else "miss").inc()
    return json.loads(row["value"]) if hit else None


async def put(namespace: str, payload: Any, value: Any, ttl_s: float = 7 * 86400) -> None:
    await get_db().execute(
        "insert or replace into cache(key, value, expires) values (?, ?, ?)",
        (_key(namespace, payload), json.dumps(value, default=str), time.time() + ttl_s),
    )
