"""Chooses which LLM each agent talks to.

* ``fast``      - cheap, low-latency model for extraction-style agents
* ``reasoning`` - larger model for judgement-heavy agents (scoring, sizing ...)

If both Groq and OpenRouter keys are configured, the secondary provider is
attached as an automatic fallback, so a rate-limit or outage on one provider
does not kill a whole 12-agent run.

Tests swap the factory with ``set_llm_factory`` so no network is needed.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from core.config import get_settings

Role = Literal["fast", "reasoning"]
_factory_override: Callable[[Role], object] | None = None


def set_llm_factory(factory: Callable[[Role], object] | None) -> None:
    """Inject a fake model factory (used by tests and the offline demo)."""
    global _factory_override
    _factory_override = factory


def _groq(model: str):
    from langchain_groq import ChatGroq

    s = get_settings()
    return ChatGroq(
        model=model,
        temperature=0,
        api_key=s.groq_api_key,
        timeout=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )


def _openrouter(model: str):
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(
        model=model,
        temperature=0.1,
        api_key=s.openrouter_api_key,
        base_url="https://openrouter.ai/api/v1",
        timeout=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )


def available_providers() -> list[str]:
    s = get_settings()
    providers = []
    if s.groq_api_key:
        providers.append("groq")
    if s.openrouter_api_key:
        providers.append("openrouter")
    # honour preferred provider order
    providers.sort(key=lambda p: p != s.llm_provider)
    return providers


def get_llms(role: Role = "reasoning") -> list:
    """Return [primary, *fallbacks] chat models for a role."""
    if _factory_override is not None:
        return [_factory_override(role)]

    s = get_settings()
    providers = available_providers()
    if not providers:
        raise RuntimeError(
            "No LLM provider configured. Set GROQ_API_KEY and/or OPENROUTER_API_KEY."
        )

    def build(provider: str):
        if provider == "groq":
            return _groq(s.fast_model if role == "fast" else s.reasoning_model)
        return _openrouter(s.openrouter_model)

    return [build(p) for p in providers]  # primary first, then fallbacks


def get_structured_llm(role: Role, schema):
    """A runnable that returns ``schema`` instances, with provider fallback."""
    primary, *fallbacks = [m.with_structured_output(schema) for m in get_llms(role)]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary
