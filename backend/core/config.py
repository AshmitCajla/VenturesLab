"""Central configuration. Every secret comes from the environment (or a local,
git-ignored .env file) - never from source code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

try:  # optional in production, handy locally
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # LLM providers (at least one key is required for real runs)
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "groq"))
    fast_model: str = field(default_factory=lambda: _env("FAST_MODEL", "openai/gpt-oss-120b"))
    reasoning_model: str = field(default_factory=lambda: _env("REASONING_MODEL", "openai/gpt-oss-120b"))
    openrouter_model: str = field(
        default_factory=lambda: _env("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
    )
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "60")))
    llm_max_retries: int = field(default_factory=lambda: int(_env("LLM_MAX_RETRIES", "2")))

    # Retrieval
    embeddings_backend: str = field(default_factory=lambda: _env("EMBEDDINGS", "fastembed"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL"))
    qdrant_api_key: str = field(default_factory=lambda: _env("QDRANT_API_KEY"))

    # Persistence
    supabase_url: str = field(default_factory=lambda: _env("SUPABASE_URL"))
    supabase_key: str = field(default_factory=lambda: _env("SUPABASE_KEY"))
    results_dir: str = field(default_factory=lambda: _env("RESULTS_DIR", "./data/results"))

    # API guard rails
    max_upload_mb: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_MB", "15")))
    max_concurrent_jobs: int = field(default_factory=lambda: int(_env("MAX_CONCURRENT_JOBS", "3")))
    build_threshold: float = field(default_factory=lambda: float(_env("BUILD_THRESHOLD", "0.65")))
    web_search_enabled: bool = field(default_factory=lambda: _env("WEB_SEARCH", "1") == "1")
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "*"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
