import os
import sys
from pathlib import Path

os.environ.setdefault("EMBEDDINGS", "hash")
os.environ.setdefault("WEB_SEARCH", "0")
os.environ.setdefault("RESULTS_DIR", "/tmp/ventureslab-test-results")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from core import model_router  # noqa: E402
from tests.fakes import FakeLLM  # noqa: E402


@pytest.fixture
def fake_llm():
    llm = FakeLLM()
    model_router.set_llm_factory(lambda role: llm)
    yield llm
    model_router.set_llm_factory(None)
