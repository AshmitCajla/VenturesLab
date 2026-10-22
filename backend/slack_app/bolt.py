"""Bolt wiring for the Slack bot (Socket Mode: no public URL needed).

Started by the API when SLACK_BOT_TOKEN and SLACK_APP_TOKEN are set, so runs,
metrics and the ops agent all live in one process. App manifest:
``slack_app/manifest.yml``.
"""
from __future__ import annotations

import asyncio
import logging
import re

from core.config import get_settings
from services import digest
from slack_app import handlers

logger = logging.getLogger("slack")
_handler = None  # keep the Socket Mode connection referenced for the life of the process


def build_app():
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=get_settings().slack_bot_token)

    @app.event("message")
    async def _message(event):
        await handlers.on_message(event)

    @app.event("app_mention")
    async def _mention(event):
        await handlers.on_mention(event)

    @app.event("app_home_opened")
    async def _home(event):
        await handlers.on_home_opened(event["user"])

    @app.event("file_shared")
    async def _file_shared(event):
        pass  # handled through the message event, which carries the channel and thread

    for action in ("rerun", "feedback_up", "feedback_down", "correct"):
        @app.action(action)
        async def _action(ack, body):
            await ack()
            await handlers.on_action(body)

    @app.action({"action_id": "open_report"})
    async def _open(ack):
        await ack()

    @app.action({"action_id": re.compile(r"home_open_.*")})
    async def _home_open(ack):
        await ack()

    @app.view("correction")
    async def _correction(ack, body, view):
        await ack()
        await handlers.on_correction(view, body.get("user", {}).get("id"))

    @app.command("/ventureslab-digest")
    async def _digest_cmd(ack, respond):
        await ack()
        title, blocks = await digest.build()
        await respond(text=title, blocks=blocks)

    return app


async def start() -> asyncio.Task | None:
    s = get_settings()
    if not (s.slack_bot_token and s.slack_app_token):
        return None
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    global _handler
    _handler = AsyncSocketModeHandler(build_app(), s.slack_app_token)
    await _handler.connect_async()
    logger.info("Slack bot connected (Socket Mode)")
    return asyncio.create_task(digest.scheduler())
