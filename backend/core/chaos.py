"""Fault injection for chaos tests. Inert unless CHAOS=1.

Faults are switched on through ``POST /chaos`` (or ``activate`` in tests) with a
probability and a duration, and are checked at the few places where real
failures happen: LLM calls, web search and deck parsing.

    llm_timeout     an LLM call raises TimeoutError
    llm_slow        an LLM call is delayed by ``delay_s``
    provider_down   every call to ``provider`` ("primary" = first in the chain) fails (exercises fallback)
    search_down     web search raises
    deck_corrupt    the uploaded deck cannot be parsed
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field

from core.config import get_settings

FAULTS = ("llm_timeout", "llm_slow", "provider_down", "search_down", "deck_corrupt")


class InjectedFault(RuntimeError):
    pass


@dataclass
class Fault:
    name: str
    probability: float = 1.0
    until: float = 0.0          # epoch seconds; 0 = until cleared
    params: dict = field(default_factory=dict)

    def live(self) -> bool:
        return not self.until or time.time() < self.until

    def to_dict(self) -> dict:
        return {"name": self.name, "probability": self.probability, "params": self.params,
                "expires_in_s": round(self.until - time.time()) if self.until else None}


_active: dict[str, Fault] = {}


def enabled() -> bool:
    return get_settings().chaos_enabled


def activate(name: str, probability: float = 1.0, duration_s: float = 0, **params) -> Fault:
    if name not in FAULTS:
        raise ValueError(f"unknown fault {name!r}; choose from {FAULTS}")
    f = Fault(name, probability, time.time() + duration_s if duration_s else 0.0, params)
    _active[name] = f
    return f


def clear(name: str | None = None) -> None:
    if name:
        _active.pop(name, None)
    else:
        _active.clear()


def active() -> list[dict]:
    return [f.to_dict() for f in _active.values() if f.live()]


def _hit(name: str) -> Fault | None:
    if not enabled():
        return None
    f = _active.get(name)
    if f and f.live() and random.random() < f.probability:
        return f
    return None


async def before_llm_call(provider: str, primary: bool = True) -> None:
    if f := _hit("provider_down"):
        target = f.params.get("provider", "primary")
        if target == provider or (target == "primary" and primary):
            raise InjectedFault(f"chaos: provider {provider} is down")
    if f := _hit("llm_slow"):
        await asyncio.sleep(float(f.params.get("delay_s", 20)))
    if _hit("llm_timeout"):
        raise TimeoutError("chaos: injected LLM timeout")


def before_search() -> None:
    if _hit("search_down"):
        raise InjectedFault("chaos: web search is down")


def before_deck_parse() -> None:
    if _hit("deck_corrupt"):
        raise InjectedFault("chaos: deck could not be parsed")
