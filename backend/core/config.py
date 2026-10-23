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


def _vision_default() -> str:
    v = _env("VISION_MODEL")
    if v.lower() == "off":
        return ""
    if v:
        return v
    return "openrouter:nvidia/nemotron-3.5-super-vl:free" if _env("OPENROUTER_API_KEY") else ""


@dataclass(frozen=True)
class Settings:
    # LLM providers (at least one key is required for real runs)
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    openrouter_api_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "groq"))
    fast_model: str = field(default_factory=lambda: _env("FAST_MODEL", "openai/gpt-oss-20b"))
    reasoning_model: str = field(default_factory=lambda: _env("REASONING_MODEL", "openai/gpt-oss-120b"))
    # Fallback models on OpenRouter, per role, comma-separated and tried in order. Free models come and
    # go (a live chaos test found the previous default returning 404), so there are two, and the canary
    # (ops/canary.py) checks every model in the chain. With OpenRouter credits, openai/gpt-oss-20b and
    # openai/gpt-oss-120b keep the fallback in the same model family. OPENROUTER_MODEL overrides both.
    openrouter_model: str = field(default_factory=lambda: _env("OPENROUTER_MODEL"))
    openrouter_fast_model: str = field(
        default_factory=lambda: _env(
            "OPENROUTER_FAST_MODEL", "nvidia/nemotron-3.5-lightning:free,nvidia/nemotron-3-ultra-550b-a55b:free"
        )
    )
    openrouter_reasoning_model: str = field(
        default_factory=lambda: _env(
            "OPENROUTER_REASONING_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3.5-lightning:free"
        )
    )
    canary_interval_s: float = field(default_factory=lambda: float(_env("CANARY_INTERVAL_S", "900")))
    # Second-pass screen for paraphrased injections (fast model, batched); 0 to disable
    semantic_screen: bool = field(default_factory=lambda: _env("SEMANTIC_SCREEN", "1") == "1")
    # Vision model for charts, tables and images in decks ("provider:model"). Defaults to a free
    # OpenRouter vision model when an OpenRouter key is set; VISION_MODEL=off disables it.
    vision_model: str = field(default_factory=lambda: _vision_default())
    vision_max_slides: int = field(default_factory=lambda: int(_env("VISION_MAX_SLIDES", "8")))
    # Client-side pacing under per-model tokens-per-minute limits (Groq free tier: 8000). 0 = off.
    groq_tpm: int = field(default_factory=lambda: int(_env("GROQ_TPM", "8000")))
    llm_timeout_s: float = field(default_factory=lambda: float(_env("LLM_TIMEOUT_S", "60")))
    llm_max_retries: int = field(default_factory=lambda: int(_env("LLM_MAX_RETRIES", "4")))

    # Retrieval
    embeddings_backend: str = field(default_factory=lambda: _env("EMBEDDINGS", "fastembed"))
    embedding_model: str = field(default_factory=lambda: _env("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5"))
    qdrant_url: str = field(default_factory=lambda: _env("QDRANT_URL"))
    qdrant_api_key: str = field(default_factory=lambda: _env("QDRANT_API_KEY"))

    # Persistence
    supabase_url: str = field(default_factory=lambda: _env("SUPABASE_URL"))
    supabase_key: str = field(default_factory=lambda: _env("SUPABASE_KEY"))
    results_dir: str = field(default_factory=lambda: _env("RESULTS_DIR", "./data/results"))
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "./data/ventureslab.db"))

    # API guard rails
    max_upload_mb: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_MB", "15")))
    max_concurrent_jobs: int = field(default_factory=lambda: int(_env("MAX_CONCURRENT_JOBS", "3")))
    build_threshold: float = field(default_factory=lambda: float(_env("BUILD_THRESHOLD", "0.65")))
    web_search_enabled: bool = field(default_factory=lambda: _env("WEB_SEARCH", "1") == "1")
    # Prompt-injection defences (services/security.py). 0 only to measure attack success without them.
    injection_defenses: bool = field(default_factory=lambda: _env("INJECTION_DEFENSES", "1") == "1")
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "*"))
    # Public deployments: comma-separated API keys (empty = open, for local development)
    api_keys: str = field(default_factory=lambda: _env("API_KEYS"))
    # /metrics without a key: on for the VM stack (Prometheus scrapes it inside the network and Caddy
    # blocks it publicly); off where the API itself is public, e.g. Render
    public_metrics: bool = field(default_factory=lambda: _env("PUBLIC_METRICS", "1") == "1")
    # seconds a shutdown waits for analyses in progress (container stop timeouts must be longer)
    shutdown_grace_s: float = field(default_factory=lambda: float(_env("SHUTDOWN_GRACE_S", "120")))
    rate_limit_runs_per_hour: int = field(default_factory=lambda: int(_env("RATE_LIMIT_RUNS_PER_HOUR", "20")))

    # Agent system
    planner_enabled: bool = field(default_factory=lambda: _env("PLANNER", "1") == "1")
    run_budget_usd: float = field(default_factory=lambda: float(_env("RUN_BUDGET_USD", "0.25")))
    verification_enabled: bool = field(default_factory=lambda: _env("VERIFICATION", "1") == "1")

    # Observability
    otel_endpoint: str = field(default_factory=lambda: _env("OTEL_EXPORTER_OTLP_ENDPOINT"))
    log_json: bool = field(default_factory=lambda: _env("LOG_JSON", "0") == "1")
    # JSON log file read by the ops agent's recent_logs tool (empty = no file)
    log_dir: str = field(default_factory=lambda: _env("LOG_DIR", "./data/logs"))
    prometheus_url: str = field(default_factory=lambda: _env("PROMETHEUS_URL", "http://localhost:9090"))
    jaeger_url: str = field(default_factory=lambda: _env("JAEGER_URL", "http://localhost:16686"))
    alert_webhook_token: str = field(default_factory=lambda: _env("ALERT_WEBHOOK_TOKEN"))
    runbook_dir: str = field(default_factory=lambda: _env("RUNBOOK_DIR"))
    # SLOs (see deploy/prometheus/rules.yml; keep the two in sync)
    slo_success_target: float = field(default_factory=lambda: float(_env("SLO_SUCCESS_TARGET", "0.99")))
    slo_latency_target_s: float = field(default_factory=lambda: float(_env("SLO_LATENCY_TARGET_S", "450")))
    slo_latency_quantile: float = field(default_factory=lambda: float(_env("SLO_LATENCY_QUANTILE", "0.95")))
    slo_complete_target: float = field(default_factory=lambda: float(_env("SLO_COMPLETE_TARGET", "0.95")))

    # Slack
    slack_bot_token: str = field(default_factory=lambda: _env("SLACK_BOT_TOKEN"))
    slack_app_token: str = field(default_factory=lambda: _env("SLACK_APP_TOKEN"))
    slack_ops_channel: str = field(default_factory=lambda: _env("SLACK_OPS_CHANNEL"))
    slack_digest_channel: str = field(default_factory=lambda: _env("SLACK_DIGEST_CHANNEL"))
    public_ui_url: str = field(default_factory=lambda: _env("PUBLIC_UI_URL", "http://localhost:8501"))
    public_api_url: str = field(default_factory=lambda: _env("PUBLIC_API_URL", "http://localhost:8000"))

    # Fault injection for chaos tests (never enable in production)
    chaos_enabled: bool = field(default_factory=lambda: _env("CHAOS", "0") == "1")


@lru_cache
def get_settings() -> Settings:
    return Settings()
