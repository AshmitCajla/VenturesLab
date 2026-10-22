"""Chooses which LLM each agent talks to.

* ``fast``      - cheap, low-latency model for extraction-style agents
* ``reasoning`` - larger model for judgement-heavy agents (scoring, sizing ...)

Each role resolves to an ordered *chain* of targets (primary first, then
fallbacks on other providers). The caller walks the chain itself instead of
using LangChain's ``with_fallbacks``, so every answer records which provider and
model produced it, its token usage and its estimated cost. Without that, a
fallback silently changes the analyst and the cost dashboard lies.

Tests swap the factory with ``set_llm_factory`` so no network is needed.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from core.config import get_settings
from core.pricing import estimate_cost

Role = Literal["fast", "reasoning"]
_factory_override: Callable[[Role], object] | None = None


def set_llm_factory(factory: Callable[[Role], object] | None) -> None:
    """Inject a fake model factory (used by tests and the offline demo)."""
    global _factory_override
    _factory_override = factory


@dataclass
class Target:
    provider: str
    model: str
    runnable: Any  # structured-output runnable

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class Usage:
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    recovered: list[str] = field(default_factory=list)  # errors retried or fallen back from on the way

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.provider, self.model, self.tokens_in, self.tokens_out)

    def to_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "cost_usd": round(self.cost_usd, 6)}


def _groq(model: str):
    from langchain_groq import ChatGroq

    s = get_settings()
    return ChatGroq(model=model, temperature=0, api_key=s.groq_api_key,
                    timeout=s.llm_timeout_s, max_retries=s.llm_max_retries)


def _openrouter(model: str):
    from langchain_openai import ChatOpenAI

    s = get_settings()
    return ChatOpenAI(model=model, temperature=0.1, api_key=s.openrouter_api_key,
                      base_url="https://openrouter.ai/api/v1",
                      timeout=s.llm_timeout_s, max_retries=s.llm_max_retries)


def available_providers() -> list[str]:
    s = get_settings()
    providers = []
    if s.groq_api_key:
        providers.append("groq")
    if s.openrouter_api_key:
        providers.append("openrouter")
    providers.sort(key=lambda p: p != s.llm_provider)  # preferred provider first
    return providers


def models_for(provider: str, role: Role) -> list[str]:
    s = get_settings()
    if provider == "groq":
        return [s.fast_model if role == "fast" else s.reasoning_model]
    spec = s.openrouter_model or (s.openrouter_fast_model if role == "fast" else s.openrouter_reasoning_model)
    return [m.strip() for m in spec.split(",") if m.strip()]


def get_llms(role: Role = "reasoning") -> list[tuple[str, str, Any]]:
    """[(provider, model, chat model)], primary first."""
    if _factory_override is not None:
        return [("fake", role, _factory_override(role))]
    providers = available_providers()
    if not providers:
        raise RuntimeError("No LLM provider configured. Set GROQ_API_KEY and/or OPENROUTER_API_KEY.")
    out = []
    for p in providers:
        for model in models_for(p, role):
            out.append((p, model, _groq(model) if p == "groq" else _openrouter(model)))
    return out


def _structured(model, schema):
    """Native structured outputs (response_format=json_schema) instead of forced tool calls.

    Reasoning models such as gpt-oss sometimes answer in prose when a tool call is
    forced, which Groq rejects with 400 "tool_use_failed". Groq's strict mode makes the
    output follow the schema exactly; OpenRouter models get the non-strict variant.
    ``include_raw`` keeps the raw message so token usage can be recorded.
    """
    try:
        from langchain_groq import ChatGroq
    except ImportError:  # pragma: no cover
        ChatGroq = ()  # noqa: N806
    if isinstance(model, ChatGroq):
        return model.with_structured_output(schema, method="json_schema", strict=True, include_raw=True)
    return model.with_structured_output(schema, method="json_schema", include_raw=True)


def get_chain(role: Role, schema) -> list[Target]:
    return [Target(p, m, _structured(llm, schema)) for p, m, llm in get_llms(role)]


def unpack(output: Any, schema, target: Target) -> tuple[Any, Usage]:
    """Normalise an ``include_raw`` result (or a bare object from fakes) into (parsed, usage)."""
    usage = Usage(target.provider, target.model)
    if isinstance(output, dict) and "parsed" in output:
        if output.get("parsing_error") is not None or output["parsed"] is None:
            raise ValueError(f"unparseable model output: {output.get('parsing_error')}")
        meta = getattr(output.get("raw"), "usage_metadata", None) or {}
        usage.tokens_in, usage.tokens_out = meta.get("input_tokens", 0), meta.get("output_tokens", 0)
        output = output["parsed"]
    if not isinstance(output, schema):  # some providers return dicts
        output = schema.model_validate(output)
    return output, usage


def get_structured_llm(role: Role, schema):
    """Kept for callers that only need the primary model (no usage tracking)."""
    return get_chain(role, schema)[0].runnable
