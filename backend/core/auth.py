"""API keys and rate limits for a public deployment.

Every analysis spends real model tokens, so a deployed API must not be open.

* ``API_KEYS``: comma-separated keys. When set, every route except ``PUBLIC_PATHS``
  needs ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``. When empty (local
  development) the API stays open.
* ``RATE_LIMIT_RUNS_PER_HOUR``: new analyses per key (or per client IP when keys
  are off). Follow-up questions get five times that. Sliding one-hour window, in
  memory, which is right for a single instance; a shared store (Redis) is the next
  step with several replicas.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.config import get_settings

# /metrics stays open for Prometheus inside the private network; the Caddyfile blocks it publicly.
PUBLIC_PATHS = {"/health", "/metrics", "/ops/alerts"}  # /ops/alerts checks its own webhook token
LIMITED = {("POST", "/analyze"): 1, ("POST", "ask"): 5}


def _keys() -> list[str]:
    return [k.strip() for k in get_settings().api_keys.split(",") if k.strip()]


def presented_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def key_id(key: str) -> str:
    """Stable, non-reversible id for a key: safe to log and to store as the run's owner."""
    return "key_" + hashlib.sha256(key.encode()).hexdigest()[:10]


def valid(key: str | None) -> bool:
    return bool(key) and any(hmac.compare_digest(key, k) for k in _keys())


class SlidingWindow:
    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, who: str, limit: int, window_s: float = 3600) -> tuple[bool, int]:
        now = time.monotonic()
        q = self.hits[who]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False, int(window_s - (now - q[0])) + 1
        q.append(now)
        return True, 0


limiter = SlidingWindow()


def _limited_kind(request: Request) -> tuple[str, int] | None:
    if request.method == "POST" and request.url.path == "/analyze":
        return "analyze", LIMITED[("POST", "/analyze")]
    if request.method == "POST" and request.url.path.endswith("/ask"):
        return "ask", LIMITED[("POST", "ask")]
    return None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        caller = request.client.host if request.client else "unknown"
        if _keys() and path not in PUBLIC_PATHS:
            key = presented_key(request)
            if not valid(key):
                return JSONResponse(
                    {"detail": "missing or invalid API key"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
            caller = key_id(key)
        request.state.caller = caller
        if kind := _limited_kind(request):
            name, factor = kind
            ok, retry = limiter.allow(f"{name}:{caller}", get_settings().rate_limit_runs_per_hour * factor)
            if not ok:
                return JSONResponse(
                    {"detail": f"rate limit reached for {name}; retry in {retry}s"},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)
