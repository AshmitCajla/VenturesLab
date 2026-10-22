"""Incident lifecycle: Alertmanager webhook -> Slack incident thread -> diagnosis -> postmortem.

    firing   -> open an incident, post it to the ops channel, gather evidence over
                MCP, post the diagnosis and the runbook in the thread
    resolved -> close it, draft a postmortem, post it in the thread

Without Slack configured everything still happens and is stored; ``GET
/ops/incidents`` shows it.
"""
from __future__ import annotations

import json
import logging
import time

from core.config import get_settings
from ops import agent
from services import slack_client
from services.db import get_db

logger = logging.getLogger("ops")


def _incident_id(alert: dict) -> str:
    return f"{alert.get('fingerprint') or alert['labels'].get('alertname')}-{alert.get('startsAt', '')[:19]}"


async def _get(incident_id: str) -> dict | None:
    row = await get_db().fetchone("select * from incidents where id = ?", (incident_id,))
    if row:
        for k in ("labels", "diagnosis", "slack"):
            row[k] = json.loads(row[k]) if row[k] else None
    return row


async def list_incidents(limit: int = 20) -> list[dict]:
    rows = await get_db().fetchall("select id from incidents order by started desc limit ?", (limit,))
    return [await _get(r["id"]) for r in rows]


def diagnosis_blocks(alert_name: str, d: agent.Diagnosis, runbook: str) -> tuple[str, list[dict]]:
    url = f"https://github.com/AshmitCajla/VenturesLab/blob/main/docs/runbooks/{runbook}.md"
    text = f"Diagnosis for {alert_name}: {d.likely_cause}"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*🔎 Diagnosis* ({d.confidence} confidence)\n{d.summary}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Likely cause* (`{d.category}`): {d.likely_cause}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Evidence*\n" + "\n".join(f"• {e}" for e in d.evidence)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Next steps*\n" + "\n".join(
            f"{i}. {s}" for i, s in enumerate(d.next_steps, 1))}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Runbook: <{url}|{runbook}>"}]},
    ]
    return text, blocks


async def open_incident(alert: dict) -> dict:
    iid = _incident_id(alert)
    if existing := await _get(iid):
        return existing
    name = alert["labels"].get("alertname", "alert")
    now = time.time()
    await get_db().execute("insert into incidents (id, alert, status, started, labels) values (?, ?, 'firing', ?, ?)",
                           (iid, name, now, json.dumps({**alert.get("labels", {}), **alert.get("annotations", {})})))
    channel = get_settings().slack_ops_channel
    slack = None
    if channel:
        sev = alert["labels"].get("severity", "page")
        summary = alert.get("annotations", {}).get("summary", "")
        ts = await slack_client.post(channel, f"🚨 [{sev}] {name}: {summary}", [
            {"type": "header", "text": {"type": "plain_text", "text": f"🚨 {name}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Severity:* {sev}\n{summary}\n"
                                         "_The ops agent is investigating in this thread…_"}}])
        slack = {"channel": channel, "ts": ts}
        await get_db().execute("update incidents set slack = ? where id = ?", (json.dumps(slack), iid))

    try:
        diag, _ = await agent.diagnose(alert)
        runbook = agent.RUNBOOK_FOR.get(diag.category, "run-failures")
        record = {**diag.model_dump(), "runbook": runbook}
        await get_db().execute("update incidents set diagnosis = ? where id = ?", (json.dumps(record), iid))
        if slack and slack["ts"]:
            text, blocks = diagnosis_blocks(name, diag, runbook)
            await slack_client.post(slack["channel"], text, blocks, thread_ts=slack["ts"])
    except Exception as exc:
        logger.exception("diagnosis failed for %s", iid)
        if slack and slack["ts"]:
            await slack_client.post(slack["channel"], f"⚠️ Automatic diagnosis failed ({exc}). "
                                    "Start with docs/runbooks/run-failures.md.", thread_ts=slack["ts"])
    return await _get(iid)


async def resolve_incident(alert: dict) -> dict | None:
    iid = _incident_id(alert)
    inc = await _get(iid)
    if not inc or inc["status"] == "resolved":
        return inc
    await get_db().execute("update incidents set status = 'resolved', resolved = ? where id = ?", (time.time(), iid))
    inc = await _get(iid)
    try:
        md = await agent.postmortem(inc, await agent.gather_evidence())
        await get_db().execute("update incidents set postmortem = ? where id = ?", (md, iid))
        if inc.get("slack") and inc["slack"].get("ts"):
            await slack_client.post(inc["slack"]["channel"], "✅ Resolved. Draft postmortem:\n" + md[:2800],
                                    thread_ts=inc["slack"]["ts"])
    except Exception:
        logger.exception("postmortem failed for %s", iid)
    return await _get(iid)


async def handle_alertmanager(payload: dict) -> list[dict]:
    out = []
    for alert in payload.get("alerts", []):
        if alert.get("status") == "resolved":
            out.append(await resolve_incident(alert))
        else:
            out.append(await open_incident(alert))
    return out
