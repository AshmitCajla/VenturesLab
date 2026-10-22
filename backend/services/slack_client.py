"""Thin wrapper around Slack's Web API, shared by the bot, incidents and the digest.

Every call is a no-op (returning None) when SLACK_BOT_TOKEN is not set, so the
rest of the system never has to check. Tests replace ``_client`` with a fake.
"""

from __future__ import annotations

import logging

from core.config import get_settings

logger = logging.getLogger("slack")
_client = None


def client():
    global _client
    if _client is None and get_settings().slack_bot_token:
        from slack_sdk.web.async_client import AsyncWebClient

        _client = AsyncWebClient(token=get_settings().slack_bot_token)
    return _client


def set_client(c) -> None:
    global _client
    _client = c


async def post(channel: str, text: str, blocks: list[dict] | None = None, thread_ts: str | None = None) -> str | None:
    c = client()
    if not c:
        return None
    try:
        res = await c.chat_postMessage(channel=channel, text=text, blocks=blocks, thread_ts=thread_ts)
        return res["ts"]
    except Exception as exc:
        logger.warning("slack post failed: %s", exc)
        return None


async def update(channel: str, ts: str, text: str, blocks: list[dict] | None = None) -> None:
    c = client()
    if not c:
        return
    try:
        await c.chat_update(channel=channel, ts=ts, text=text, blocks=blocks)
    except Exception as exc:
        logger.warning("slack update failed: %s", exc)
