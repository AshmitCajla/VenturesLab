"""Fallback canary: probe every model in every provider chain on a schedule.

A fallback that is broken is invisible until the day the primary fails, which is
exactly when it is needed. (A live chaos test found the default free fallback
returning 404 for days.) Every ``CANARY_INTERVAL_S`` each target gets a tiny
structured request; the result is a Prometheus gauge per target, and
``FallbackModelDown`` alerts on it (deploy/prometheus/rules.yml).
"""
from __future__ import annotations

import asyncio
import logging
import time

from prometheus_client import Gauge
from pydantic import BaseModel

from core import model_router
from core.config import get_settings
from core.model_router import Target, _structured, unpack

logger = logging.getLogger("canary")

TARGET_UP = Gauge("ventureslab_llm_target_up", "1 if the model answered the canary", ["role", "provider", "model"])
TARGET_LATENCY = Gauge("ventureslab_llm_target_canary_seconds", "Canary latency", ["role", "provider", "model"])
_last: dict[str, dict] = {}


class Ping(BaseModel):
    ok: bool


async def probe_once(timeout_s: float = 60) -> list[dict]:
    results = []
    for role in ("fast", "reasoning"):
        for provider, model, llm in model_router.get_llms(role):
            target = Target(provider, model, _structured(llm, Ping))
            started = time.perf_counter()
            try:
                out = await asyncio.wait_for(target.runnable.ainvoke('Reply with {"ok": true}.'), timeout_s)
                unpack(out, Ping, target)
                up, error = 1, None
            except Exception as exc:
                up, error = 0, f"{type(exc).__name__}: {str(exc)[:160]}"
            elapsed = time.perf_counter() - started
            TARGET_UP.labels(role, provider, model).set(up)
            TARGET_LATENCY.labels(role, provider, model).set(elapsed)
            row = {"role": role, "provider": provider, "model": model, "up": bool(up),
                   "seconds": round(elapsed, 1), "error": error, "checked": time.time()}
            _last[f"{role}:{provider}:{model}"] = row
            results.append(row)
            if not up:
                logger.warning("canary: %s %s:%s is down (%s)", role, provider, model, error)
    return results


def last_results() -> list[dict]:
    return list(_last.values())


async def loop() -> None:
    interval = get_settings().canary_interval_s
    if interval <= 0:
        return
    await asyncio.sleep(30)  # let the API finish starting
    while True:
        try:
            await probe_once()
        except Exception:
            logger.exception("canary failed")
        await asyncio.sleep(interval)
