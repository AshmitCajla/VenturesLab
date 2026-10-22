"""The single path every structured LLM call takes.

Walks the provider chain for a role (primary, then fallbacks), retries with the
wait the provider asks for, and records for each call: an OTel span, Prometheus
metrics, token usage and estimated cost (added to the run's budget). Chaos
faults are injected here too, so fallback and degradation can be tested.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from core import chaos, runctx
from core.model_router import Role, Target, Usage, get_chain, unpack
from core.telemetry import LLM_CALLS, LLM_COST, LLM_LATENCY, LLM_TOKENS, span

logger = logging.getLogger("llm")

RETRY_DELAY_S = 2.0   # tests set this to 0
OUTPUT_TOKENS_EST = 1500  # gpt-oss output includes reasoning tokens, which count against TPM


class TokenPacer:
    """Sliding 60-second token budget per model, so parallel agents queue politely instead of
    tripping the provider's tokens-per-minute limit and burning retries on 429s."""

    def __init__(self, tpm: int):
        self.tpm = tpm
        self.window: list[tuple[float, int]] = []
        self.lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> float:
        tokens = min(tokens, self.tpm)
        waited = 0.0
        async with self.lock:
            while True:
                now = time.monotonic()
                self.window = [(t, n) for t, n in self.window if now - t < 60]
                used = sum(n for _, n in self.window)
                if used + tokens <= self.tpm or not self.window:
                    self.window.append((now, tokens))
                    return waited
                pause = 60 - (now - self.window[0][0]) + 0.05
                waited += pause
                await asyncio.sleep(pause)


_pacers: dict[tuple[int, str], TokenPacer] = {}


def pacer_for(target: Target) -> TokenPacer | None:
    from core.config import get_settings

    tpm = get_settings().groq_tpm if target.provider == "groq" else 0
    if tpm <= 0:
        return None
    key = (id(asyncio.get_running_loop()), target.label)
    if key not in _pacers:
        _pacers[key] = TokenPacer(tpm)
    return _pacers[key]


def estimate_tokens(prompt) -> int:
    return len(str(prompt)) // 4 + OUTPUT_TOKENS_EST
MAX_WAIT_S = 65.0
_WAIT_RE = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s")


def _wait_hint(exc: Exception, attempt: int) -> float:
    """Groq says how long to wait ("try again in 7.5s"); otherwise back off linearly."""
    m = _WAIT_RE.search(str(exc))
    if m:
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + float(m.group(3)) + 1
    return RETRY_DELAY_S * (attempt + 1)


_PERMANENT = re.compile(r"NotFound|\b404\b|\b401\b|\b403\b|PermissionDenied|Authentication|"
                        r"only available on|model_not_found|does not exist|decommissioned", re.IGNORECASE)


def is_permanent(exc: Exception) -> bool:
    """Errors that retrying the same target cannot fix (wrong model, no access, retired model)."""
    return bool(_PERMANENT.search(f"{type(exc).__name__}: {exc}")) and not _WAIT_RE.search(str(exc))


async def call_structured(role: Role, schema, prompt, name: str, attempts: int = 5):
    """Returns (parsed schema instance, Usage). Raises the last error if every attempt fails."""
    return await call_targets(get_chain(role, schema), schema, prompt, name, attempts)


async def call_targets(chain: list[Target], schema, prompt, name: str, attempts: int = 5):
    """``prompt`` is a string or a list of chat messages (e.g. text plus an image).

    Each round tries every live target in order. A target that fails permanently (404, no access)
    is dropped for the rest of this call; otherwise the round waits as long as the soonest
    retryable target asks (Groq says "try again in 7.5s") before trying again.
    """
    ctx = runctx.current()
    live = list(chain)
    last: Exception | None = None
    errors: dict[int, Exception] = {}  # by chain position; the primary's error is the useful one
    recovered: list[Exception] = []      # reported even on success: absorbed faults must stay visible
    for attempt in range(attempts):
        waits = []
        for target in list(live):
            started = time.perf_counter()
            with span("llm.call", **{"llm.provider": target.provider, "llm.model": target.model,
                                     "agent": name, "attempt": attempt + 1}) as s:
                try:
                    if pacer := pacer_for(target):
                        if waited := await pacer.acquire(estimate_tokens(prompt)):
                            s.set_attribute("llm.paced_s", round(waited, 1))
                    await chaos.before_llm_call(target.provider, primary=target is chain[0])
                    parsed, usage = unpack(await target.runnable.ainvoke(prompt), schema, target)
                except Exception as exc:
                    last = errors[chain.index(target)] = exc
                    recovered.append(exc)
                    LLM_CALLS.labels(target.provider, target.model, "error").inc()
                    s.set_attribute("error", f"{type(exc).__name__}: {str(exc)[:200]}")
                    if is_permanent(exc):
                        live.remove(target)
                        logger.warning("%s: %s unusable, dropped (%s)", name, target.label, str(exc)[:160])
                    else:
                        waits.append(_wait_hint(exc, attempt))
                        logger.warning("%s: %s failed (%s)", name, target.label, str(exc)[:160])
                    continue
                elapsed = time.perf_counter() - started
                usage.recovered = [f"{type(e).__name__}: {str(e)[:200]}" for e in recovered]
                _record(usage, elapsed, name, ctx)
                s.set_attribute("llm.tokens_in", usage.tokens_in)
                s.set_attribute("llm.tokens_out", usage.tokens_out)
                return parsed, usage
        if not live:
            break
        if attempt < attempts - 1:
            wait = min(min(waits), MAX_WAIT_S) if RETRY_DELAY_S else 0
            logger.warning("%s: all providers failed (attempt %d); retrying in %.0fs", name, attempt + 1, wait)
            await asyncio.sleep(wait)
    assert last is not None
    raise errors.get(0, last)


def _record(usage: Usage, elapsed: float, name: str, ctx: runctx.RunContext | None) -> None:
    p, m = usage.provider, usage.model
    LLM_CALLS.labels(p, m, "ok").inc()
    LLM_LATENCY.labels(p, m).observe(elapsed)
    LLM_TOKENS.labels(p, m, "in").inc(usage.tokens_in)
    LLM_TOKENS.labels(p, m, "out").inc(usage.tokens_out)
    LLM_COST.labels(p, m).inc(usage.cost_usd)
    if ctx:
        ctx.record({**usage.to_dict(), "agent": name, "ms": round(elapsed * 1000)})
