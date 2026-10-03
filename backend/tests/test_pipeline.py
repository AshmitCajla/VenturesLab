import copy

import schema as S
from agents.agent_09_scoring import recommendation_for, weighted_score
from core import model_router
from evaluation.checks import run_checks
from services.documents import Chunk, chunk_text
from services.retrieval import InMemoryStore
from tests.fakes import GOOD, FakeLLM
from workflow.graph import AGENTS, compiled_graph


async def run(state=None):
    return await compiled_graph.ainvoke(
        {"session_id": "t", "idea": "Clinic scheduling on WhatsApp", **(state or {})})


async def test_full_run_executes_all_12_agents(fake_llm):
    out = await run()
    ran = {t["agent"] for t in out["trace"] if t["status"] == "ok"}
    assert len(ran) == 12 and len(AGENTS) == 12
    assert out["recommendation"] == "BUILD"
    assert out["financial_model"]["year_5_revenue_usd"] == 3e6
    assert all(e["passed"] for e in out["evaluations"] if e["severity"] == "error")


async def test_low_score_skips_builder_agents():
    low = copy.deepcopy(GOOD[S.ScoringResult])
    for k in S.SCORING_WEIGHTS:
        low[k]["score"] = 3
    llm = FakeLLM({S.ScoringResult: [low]})
    model_router.set_llm_factory(lambda role: llm)
    try:
        out = await run()
    finally:
        model_router.set_llm_factory(None)
    assert out["recommendation"] == "PASS"
    assert "roadmap" not in out and "financial_model" not in out


async def test_self_correction_retries_bad_market_sizing():
    bad = dict(GOOD[S.MarketSizing], som_usd=5e9)  # SOM > SAM: must be rejected
    llm = FakeLLM({S.MarketSizing: [bad]})
    model_router.set_llm_factory(lambda role: llm)
    try:
        out = await run()
    finally:
        model_router.set_llm_factory(None)
    trace = next(t for t in out["trace"] if t["output"] == "market_size")
    assert trace["attempts"] == 2
    assert out["market_size"]["som_usd"] == GOOD[S.MarketSizing]["som_usd"]
    retry_prompt = [p for name, p in llm.prompts if name == "MarketSizing"][-1]
    assert "FAILED THESE QUALITY CHECKS" in retry_prompt


async def test_agent_failure_is_isolated():
    llm = FakeLLM({S.PESTLEAnalysis: [RuntimeError("provider down")] * 4})
    model_router.set_llm_factory(lambda role: llm)
    try:
        out = await run()
    finally:
        model_router.set_llm_factory(None)
    assert "error" in out["pestle"]
    assert out["recommendation"] == "BUILD"  # rest of the pipeline still completed


async def test_profile_failure_skips_dependents():
    llm = FakeLLM({S.VentureProfile: [RuntimeError("bad key")] * 4})
    model_router.set_llm_factory(lambda role: llm)
    try:
        out = await run()
    finally:
        model_router.set_llm_factory(None)
    skipped = [t for t in out["trace"] if t["status"] == "skipped"]
    assert len(skipped) >= 8


async def test_rag_context_reaches_financial_agent(fake_llm, monkeypatch):
    import agents.base as base

    store = InMemoryStore()
    monkeypatch.setattr(base, "get_store", lambda: store)
    await store.index("t", [Chunk("Team of four engineers from IIT.", 1),
                            Chunk("We project revenue of $2M ARR with burn rate of $40k per month.", 9)])
    await run({"deck_indexed": True})
    fin_prompt = next(p for name, p in fake_llm.prompts if name == "FinancialModel")
    assert "burn rate of $40k" in fin_prompt


def test_weighted_score_and_thresholds():
    all_tens = {k: {"score": 10} for k in S.SCORING_WEIGHTS}
    all_ones = {k: {"score": 1} for k in S.SCORING_WEIGHTS}
    assert weighted_score(all_tens) == 1.0 and weighted_score(all_ones) == 0.0
    assert recommendation_for(0.7) == "BUILD"
    assert recommendation_for(0.5) == "PIVOT"
    assert recommendation_for(0.2) == "PASS"


def test_checks_catch_known_failure_modes():
    comps = [dict(company="ClinicFlow"), dict(company="X"), dict(company="x")]
    res = {c.name: c.passed for c in run_checks("competitors", comps,
                                                  {"venture_profile": {"name": "ClinicFlow"}})}
    assert res == {"count_3_to_7": True, "unique_names": False, "not_self": False}

    cv = dict(GOOD[S.CustomerValidation], real_pain_or_nice_to_have="nice to have", customer_pain_score=9)
    assert not run_checks("customer_validation", cv, {})[0].passed


def test_chunking_overlaps():
    chunks = chunk_text(" ".join(str(i) for i in range(400)), max_words=180, overlap=40)
    assert len(chunks) == 3 and chunks[1].split()[0] == "140"


async def test_transient_model_error_is_retried():
    llm = FakeLLM({S.VentureProfile: [RuntimeError("tool_use_failed")]})
    model_router.set_llm_factory(lambda role: llm)
    try:
        out = await run()
    finally:
        model_router.set_llm_factory(None)
    assert out["venture_profile"]["name"] == "ClinicFlow"
    assert out["recommendation"] == "BUILD"
