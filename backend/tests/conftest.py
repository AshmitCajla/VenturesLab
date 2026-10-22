import os
import sys
from pathlib import Path

# Tests never touch the network or real accounts. Setting these *before* config loads means a
# developer's backend/.env (which load_dotenv would read) cannot leak keys or Slack tokens in.
for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_OPS_CHANNEL",
            "SLACK_DIGEST_CHANNEL", "OTEL_EXPORTER_OTLP_ENDPOINT", "QDRANT_URL", "SUPABASE_URL",
            "ALERT_WEBHOOK_TOKEN"):
    os.environ[key] = ""
os.environ.update({"EMBEDDINGS": "hash", "WEB_SEARCH": "0", "RESULTS_DIR": "/tmp/ventureslab-test-results",
                   "DB_PATH": ":memory:", "LOG_DIR": "", "VISION_MODEL": "off", "CHAOS": "0", "CANARY_INTERVAL_S": "0",
                   "PROMETHEUS_URL": "http://127.0.0.1:9", "JAEGER_URL": "http://127.0.0.1:9"})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import core.llm  # noqa: E402
from core import model_router  # noqa: E402
from core.config import get_settings  # noqa: E402
from services import db  # noqa: E402
from services.runs import runs  # noqa: E402
from tests.fakes import FakeLLM  # noqa: E402

core.llm.RETRY_DELAY_S = 0  # no real waiting in tests


@pytest.fixture(autouse=True)
def fresh_db():
    """Every test gets an empty database and run cache."""
    db.reset_db(":memory:")
    runs.active.clear()
    yield


@pytest.fixture
def fake_llm():
    llm = FakeLLM()
    model_router.set_llm_factory(lambda role: llm)
    yield llm
    model_router.set_llm_factory(None)


@pytest.fixture
def settings(monkeypatch):
    """settings(KEY="value", ...) sets env vars and reloads Settings; undone after the test."""
    def apply(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        return get_settings()
    yield apply
    monkeypatch.undo()
    get_settings.cache_clear()
