"""Slack behaviour, as plain async functions (Bolt wiring lives in slack_app/bolt.py).

* A PDF dropped in a channel the bot is in -> analysis in a thread, with one
  live progress message that is edited as agents finish, then a summary card.
* Mention with a website or a description -> the same, without a deck.
* Mention in the ops channel -> the ops agent answers ("what broke last night?").
* Reply in a run's thread -> answered from that run's data and evidence.
* Buttons: re-run, 👍/👎, "correct a section" (modal; saved as a labelled correction).
* App Home: the user's recent runs.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from urllib.parse import urlparse

import httpx

from core.config import get_settings
from core.telemetry import FEEDBACK
from services import qa, slack_client
from services.db import get_db
from services.runs import Run, runs
from slack_app import blocks
from workflow.graph import AGENTS
from workflow.runner import execute

logger = logging.getLogger("slack")
URL_RE = re.compile(r"<?(https?://[^\s|>]+)(?:\|[^>]*)?>?")
MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
UPDATE_EVERY_S = 2.0
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def download_pdf(file: dict) -> bytes:
    url = file.get("url_private_download") or file.get("url_private") or ""
    host = urlparse(url).hostname or ""
    if not (host == "files.slack.com" or host.endswith(".slack.com")):
        raise ValueError("refusing to download a file that is not hosted by Slack")
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    if (file.get("size") or 0) > max_bytes:
        raise ValueError(f"the PDF is larger than {get_settings().max_upload_mb} MB")
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url, headers={"Authorization": f"Bearer {get_settings().slack_bot_token}"})
        r.raise_for_status()
    data = r.content
    if len(data) > max_bytes or not data.startswith(b"%PDF"):
        raise ValueError("that file is not a PDF I can read")
    return data


def _progress_updater():
    last = {"t": 0.0}

    async def on_update(r: Run) -> None:
        s = r.slack or {}
        final = r.status in ("done", "failed")
        if not final and time.time() - last["t"] < UPDATE_EVERY_S:
            return  # Slack allows ~1 edit per second per message; batch the rest
        last["t"] = time.time()
        if s.get("progress_ts"):
            text, blks = blocks.progress(r, len(AGENTS))
            await slack_client.update(s["channel"], s["progress_ts"], text, blks)
        if r.status == "done" and not s.get("card_ts"):
            text, blks = blocks.summary_card(r)
            s["card_ts"] = await slack_client.post(s["channel"], text, blks, thread_ts=s["thread_ts"]) or "posted"

    return on_update


async def start_run(
    *,
    channel: str,
    thread_ts: str,
    user: str | None,
    idea: str = "",
    url: str = "",
    pdf: bytes | None = None,
    file: dict | None = None,
) -> Run:
    run = Run(
        id=str(uuid.uuid4()),
        source="slack",
        user_id=user,
        input={
            "idea": idea[:8000],
            "url": url,
            "filename": (file or {}).get("name"),
            "slack_file": {k: (file or {}).get(k) for k in ("id", "url_private_download", "size", "name")}
            if file
            else None,
        },
        slack={"channel": channel, "thread_ts": thread_ts},
    )
    head, blks = blocks.progress(run, len(AGENTS))
    run.slack["progress_ts"] = await slack_client.post(channel, head, blks, thread_ts=thread_ts)
    await runs.save(run)
    _spawn(execute(run, pdf, _progress_updater()))
    return run


async def run_for_thread(channel: str, thread_ts: str) -> Run | None:
    row = await get_db().fetchone(
        "select id from runs where json_extract(slack, '$.channel') = ? and json_extract(slack, '$.thread_ts') = ? "
        "order by created desc limit 1",
        (channel, thread_ts),
    )
    return await runs.get(row["id"]) if row else None


async def answer_in_thread(run: Run, channel: str, thread_ts: str, question: str) -> None:
    if run.status != "done":
        await slack_client.post(
            channel, f"The analysis is still {run.status}; ask again when it's done.", thread_ts=thread_ts
        )
        return
    history = (run.slack or {}).get("qa", [])
    try:
        ans = await qa.answer(run.result, question, history)
        text = ans.answer + ("" if ans.grounded else "\n_(Not covered by this run's data.)_")
    except Exception as exc:
        logger.exception("follow-up failed")
        text = f"Sorry, I couldn't answer that ({type(exc).__name__})."
    await slack_client.post(channel, text, thread_ts=thread_ts)
    run.slack = {
        **(run.slack or {}),
        "qa": ([*history, {"role": "analyst", "text": question}, {"role": "you", "text": text}])[-12:],
    }
    await runs.save(run)


# ------------------------------------------------------------------- events
async def on_message(event: dict) -> None:
    if event.get("bot_id") or event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    channel, ts = event.get("channel"), event.get("ts")
    pdfs = [f for f in event.get("files", []) if f.get("filetype") == "pdf" or f.get("mimetype") == "application/pdf"]
    if pdfs:
        f = pdfs[0]
        thread_ts = event.get("thread_ts") or ts
        try:
            pdf = await download_pdf(f)
        except Exception as exc:
            await slack_client.post(channel, f"I couldn't read *{f.get('name')}*: {exc}", thread_ts=thread_ts)
            return
        await start_run(
            channel=channel,
            thread_ts=thread_ts,
            user=event.get("user"),
            idea=MENTION_RE.sub("", event.get("text", "")).strip(),
            pdf=pdf,
            file=f,
        )
        return
    if event.get("thread_ts") and event["thread_ts"] != ts:
        run = await run_for_thread(channel, event["thread_ts"])
        if run and event.get("text", "").strip():
            await answer_in_thread(run, channel, event["thread_ts"], MENTION_RE.sub("", event["text"]).strip())


async def on_mention(event: dict) -> None:
    if event.get("files") or event.get("bot_id"):
        return  # file shares are handled by on_message
    channel, ts = event["channel"], event["ts"]
    text = MENTION_RE.sub("", event.get("text", "")).strip()
    thread_ts = event.get("thread_ts")
    if thread_ts and await run_for_thread(channel, thread_ts):
        return  # on_message answers thread replies; avoid answering twice
    if channel == get_settings().slack_ops_channel or text.lower().startswith("ops "):
        from ops.agent import ask_ops

        try:
            ans = await ask_ops(text.removeprefix("ops ").strip() or "How is the service doing?")
            reply = ans.answer + (f"\n_Tools: {', '.join(ans.tools_used)}_" if ans.tools_used else "")
        except Exception as exc:
            reply = f"The ops agent failed ({type(exc).__name__}: {exc})"
        await slack_client.post(channel, reply, thread_ts=thread_ts or ts)
        return
    if not text:
        await slack_client.post(
            channel,
            "Drop a pitch-deck PDF here, or mention me with a website or a short description of the startup.",
            thread_ts=ts,
        )
        return
    m = URL_RE.search(text)
    url = m.group(1) if m else ""
    idea = URL_RE.sub("", text).strip()
    await start_run(channel=channel, thread_ts=thread_ts or ts, user=event.get("user"), idea=idea, url=url)


async def on_action(body: dict) -> None:
    action = body["actions"][0]
    aid, run_id = action["action_id"], action.get("value")
    user = body.get("user", {}).get("id")
    channel = body.get("channel", {}).get("id")
    thread_ts = (body.get("message") or {}).get("thread_ts") or (body.get("message") or {}).get("ts")
    if aid in ("feedback_up", "feedback_down"):
        rating = "up" if aid == "feedback_up" else "down"
        await runs.add_feedback(run_id, rating, user)
        FEEDBACK.labels(rating).inc()
        await slack_client.post(
            channel, f"Thanks <@{user}>, feedback recorded ({'👍' if rating == 'up' else '👎'}).", thread_ts=thread_ts
        )
    elif aid == "rerun":
        old = await runs.get(run_id)
        if not old:
            return
        pdf = None
        if (old.input or {}).get("slack_file"):
            try:
                pdf = await download_pdf(old.input["slack_file"])
            except Exception as exc:
                await slack_client.post(channel, f"Couldn't fetch the deck again: {exc}", thread_ts=thread_ts)
                return
        await start_run(
            channel=channel,
            thread_ts=thread_ts,
            user=user,
            idea=old.input.get("idea", ""),
            url=old.input.get("url", ""),
            pdf=pdf,
            file=old.input.get("slack_file"),
        )
    elif aid == "correct":
        c = slack_client.client()
        if c:
            await c.views_open(
                trigger_id=body["trigger_id"],
                view=blocks.correction_modal(run_id, {"channel": channel, "thread_ts": thread_ts}),
            )


async def on_correction(view: dict, user: str | None) -> None:
    run_id, channel, thread_ts = (view.get("private_metadata", "") + "||").split("|")[:3]
    values = view["state"]["values"]
    section = values["section"]["value"]["selected_option"]["value"]
    comment = values["comment"]["value"]["value"]
    await runs.add_feedback(run_id, "correction", user, section, comment)
    FEEDBACK.labels("correction").inc()
    if channel:
        await slack_client.post(
            channel,
            f"✏️ Correction to *{section.replace('_', ' ')}* saved by <@{user}>. "
            "It is now a labelled case in the evaluation set.",
            thread_ts=thread_ts or None,
        )


async def on_home_opened(user: str) -> None:
    c = slack_client.client()
    if not c:
        return
    recent = await runs.list(user_id=user, limit=10)
    stats = await runs.stats(time.time() - 7 * 86400)
    await c.views_publish(user_id=user, view=blocks.app_home(recent, stats))
