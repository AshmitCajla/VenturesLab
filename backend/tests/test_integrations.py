"""Slack bot, MCP servers, ops agent / incidents and the v3 API routes, all offline."""
import json
import time
import uuid

import pytest
from mcp import Client

from core.tools import decode
from services import slack_client
from services.runs import Run, runs
from tests.test_api import make_pdf, wait


class FakeSlack:
    """Records Web API calls the way slack_sdk's AsyncWebClient would receive them."""

    def __init__(self):
        self.posts, self.updates, self.views, self.homes = [], [], [], []

    async def chat_postMessage(self, **kw):
        self.posts.append(kw)
        return {"ts": f"{time.time():.6f}"}

    async def chat_update(self, **kw):
        self.updates.append(kw)
        return {"ok": True}

    async def views_open(self, **kw):
        self.views.append(kw)

    async def views_publish(self, **kw):
        self.homes.append(kw)


@pytest.fixture
def slack(settings):
    fake = FakeSlack()
    slack_client.set_client(fake)
    settings(SLACK_OPS_CHANNEL="COPS")
    yield fake
    slack_client.set_client(None)


async def _drain():
    import asyncio

    from slack_app import handlers

    while handlers._tasks:
        await asyncio.gather(*list(handlers._tasks))


# ------------------------------------------------------------------ Slack bot
async def test_pdf_in_channel_gets_live_progress_and_a_summary_card(fake_llm, slack, monkeypatch):
    from slack_app import handlers

    async def fake_download(f):
        return make_pdf("ClinicFlow\n\nClinic software is a $50B market\n\nrevenue $2M")
    monkeypatch.setattr(handlers, "download_pdf", fake_download)
    monkeypatch.setattr(handlers, "UPDATE_EVERY_S", 0)
    await handlers.on_message({"channel": "C1", "ts": "100.1", "user": "U1", "text": "",
                               "files": [{"filetype": "pdf", "name": "deck.pdf"}]})
    await _drain()
    progress = slack.posts[0]
    assert progress["thread_ts"] == "100.1" and "Queued" in progress["text"]
    assert any("Analysing" in u["text"] for u in slack.updates)
    assert "complete" in slack.updates[-1]["text"]
    card = slack.posts[-1]
    assert card["text"].startswith("ClinicFlow: BUILD")
    actions = next(b for b in card["blocks"] if b["type"] == "actions")
    assert {e["action_id"] for e in actions["elements"]} >= {"rerun", "feedback_up", "feedback_down", "correct"}
    assert (await runs.list())[0].source == "slack"


async def test_thread_follow_up_is_answered_from_the_run(fake_llm, slack, monkeypatch):
    from slack_app import handlers

    run = Run(id=str(uuid.uuid4()), status="done", source="slack", result={"score": 0.8},
              slack={"channel": "C1", "thread_ts": "200.1"})
    await runs.save(run)
    await handlers.on_message({"channel": "C1", "ts": "200.5", "thread_ts": "200.1", "user": "U1",
                               "text": "why is the score high?"})
    assert "pain is frequent" in slack.posts[-1]["text"]
    assert slack.posts[-1]["thread_ts"] == "200.1"
    assert len((await runs.get(run.id)).slack["qa"]) == 2


async def test_buttons_and_correction_modal_record_feedback(slack):
    from slack_app import handlers

    await runs.save(Run(id="r1", status="done"))
    body = {"user": {"id": "U1"}, "channel": {"id": "C1"}, "message": {"ts": "1.0"}, "trigger_id": "t",
            "actions": [{"action_id": "feedback_down", "value": "r1"}]}
    await handlers.on_action(body)
    await handlers.on_action({**body, "actions": [{"action_id": "correct", "value": "r1"}]})
    view = slack.views[0]["view"]
    view["state"] = {"values": {"section": {"value": {"selected_option": {"value": "market_size"}}},
                                "comment": {"value": {"value": "TAM is closer to $2B"}}}}
    await handlers.on_correction(view, "U1")
    rows = await runs.corrections()
    assert rows[0]["section"] == "market_size" and rows[0]["comment"] == "TAM is closer to $2B"
    stats = await runs.stats(0)
    assert stats["feedback"] == {"down": 1, "correction": 1}


async def test_app_home_lists_the_users_runs(slack):
    from slack_app import handlers

    await runs.save(Run(id="r1", status="done", user_id="U1", title="ClinicFlow", score=0.8, recommendation="BUILD"))
    await handlers.on_home_opened("U1")
    text = json.dumps(slack.homes[0]["view"])
    assert "ClinicFlow" in text and "BUILD" in text


async def test_mention_in_ops_channel_goes_to_ops_agent(fake_llm, slack):
    from slack_app import handlers

    await handlers.on_mention({"channel": "COPS", "ts": "5.0", "user": "U1", "text": "<@UBOT> what broke last night?"})
    assert "rate limits" in slack.posts[-1]["text"]
    assert "error_signatures" in slack.posts[-1]["text"]  # tools the agent used are shown


def test_slack_downloads_only_from_slack():
    import asyncio

    from slack_app.handlers import download_pdf

    with pytest.raises(ValueError, match="not hosted by Slack"):
        asyncio.run(download_pdf({"url_private_download": "http://169.254.169.254/latest/meta-data"}))


# ---------------------------------------------------------------- MCP servers
async def test_ventureslab_mcp_server_analyses_and_compares(fake_llm):
    from mcp_servers.ventureslab_server import server

    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert names == {"analyze_deck", "get_run", "list_runs", "ask_run", "compare_decks"}
        a = json.loads((await c.call_tool("analyze_deck", {"description": "Clinic scheduling"})).content[0].text)
        b = json.loads((await c.call_tool("analyze_deck", {"description": "Clinic scheduling v2"})).content[0].text)
        assert a["status"] == "done" and "ClinicFlow: BUILD" in a["report_markdown"]
        cmp = json.loads((await c.call_tool("compare_decks", {"run_id_a": a["run_id"], "run_id_b": b["run_id"]}))
                         .content[0].text)
        assert cmp["a"]["recommendation"] == "BUILD"
        bad = await c.call_tool("analyze_deck", {})
        assert bad.is_error


async def test_ops_mcp_server_exposes_observability_tools():
    from mcp_servers.ops_server import server

    await runs.save(Run(id="f1", status="failed", error="RateLimitError: Error code: 429 tokens per day",
                        created=time.time() - 30, finished=time.time()))
    async with Client(server) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert {"slo_status", "error_signatures", "recent_logs", "get_trace", "read_runbook"} <= names
        sigs = decode(await c.call_tool("error_signatures", {"hours": 1}))
        assert sigs[0]["category"] == "rate_limit"
        prom = decode(await c.call_tool("query_prometheus", {"promql": "up"}))
        assert "unavailable" in prom  # degrades instead of failing when Prometheus is absent
        book = decode(await c.call_tool("read_runbook", {"name": "llm-provider-errors"}))
        assert "Mitigate" in book


# ---------------------------------------------------------- incidents (ops agent)
ALERT = {"status": "firing", "fingerprint": "abc", "startsAt": "2026-10-22T01:00:00Z",
         "labels": {"alertname": "ReportCompletenessBudgetBurn", "severity": "page"},
         "annotations": {"summary": "Too many reports are missing sections."}}


async def test_alert_opens_incident_thread_with_diagnosis_then_postmortem(fake_llm, slack):
    from ops.incidents import handle_alertmanager, list_incidents

    [inc] = await handle_alertmanager({"alerts": [ALERT]})
    assert inc["status"] == "firing" and inc["diagnosis"]["category"] == "llm_timeout"
    assert inc["diagnosis"]["runbook"] == "high-latency"
    header, diagnosis = slack.posts[0], slack.posts[1]
    assert header["channel"] == "COPS" and "ReportCompletenessBudgetBurn" in header["text"]
    assert diagnosis["thread_ts"] == inc["slack"]["ts"]
    assert any("runbooks/high-latency.md" in json.dumps(b) for b in diagnosis["blocks"])

    await handle_alertmanager({"alerts": [{**ALERT, "status": "resolved"}]})
    [done] = await list_incidents()
    assert done["status"] == "resolved" and "Summary" in done["postmortem"]
    assert "postmortem" in slack.posts[-1]["text"]


async def test_duplicate_alert_does_not_open_a_second_incident(fake_llm, slack):
    from ops.incidents import handle_alertmanager, list_incidents

    await handle_alertmanager({"alerts": [ALERT]})
    await handle_alertmanager({"alerts": [ALERT]})
    assert len(await list_incidents()) == 1


# ------------------------------------------------------------------- API v3
@pytest.fixture
def client(fake_llm):
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c


def test_run_history_follow_up_feedback_and_report(client):
    rid = client.post("/analyze", data={"idea": "Clinic scheduling", "user": "U9"}).json()["run_id"]
    assert wait(client, rid)["status"] == "done"
    assert client.get("/runs", params={"user": "U9"}).json()[0]["run_id"] == rid
    ans = client.post(f"/runs/{rid}/ask", json={"question": "Why BUILD?"}).json()
    assert ans["grounded"] is True
    assert client.post(f"/runs/{rid}/feedback", json={"rating": "up"}).json() == {"ok": True}
    assert client.post(f"/runs/{rid}/feedback", json={"rating": "meh"}).status_code == 422
    assert client.get(f"/runs/{rid}/report.md").text.startswith("# ClinicFlow: BUILD")


def test_metrics_slo_and_guarded_chaos(client):
    rid = client.post("/analyze", data={"idea": "x"}).json()["run_id"]
    wait(client, rid)
    metrics = client.get("/metrics").text
    assert "ventureslab_runs_total" in metrics and "ventureslab_llm_calls_total" in metrics
    assert client.get("/slo").json()["windows"]["1h"]["runs"] >= 1
    assert client.post("/chaos", json={"name": "llm_timeout"}).status_code == 403


def test_alert_webhook_accepts_alertmanager_payload(client, slack):
    assert client.post("/ops/alerts", json={"alerts": [ALERT]}).json() == {"accepted": 1}


async def test_diagnosis_falls_back_to_rules_when_the_model_is_down(slack):
    from core import model_router
    from ops.agent import Diagnosis, diagnose
    from tests.fakes import FakeLLM

    await runs.save(Run(id="f1", status="failed", error="TimeoutError: chaos: injected LLM timeout",
                        created=time.time() - 30, finished=time.time()))
    model_router.set_llm_factory(lambda role: FakeLLM({Diagnosis: [RuntimeError("429 tokens per day")] * 9}))
    try:
        diag, _ = await diagnose(ALERT)
    finally:
        model_router.set_llm_factory(None)
    assert diag.category == "llm_timeout" and diag.confidence == "Low"
    assert "model unavailable" in diag.summary
