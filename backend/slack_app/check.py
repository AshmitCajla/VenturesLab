"""Check a Slack setup before starting the API.

    python -m slack_app.check            # tokens, scopes, Socket Mode, channels
    python -m slack_app.check --post     # also posts a test message to the ops channel

Exits non-zero with a specific fix for the first thing that is wrong.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from core.config import get_settings

REQUIRED_SCOPES = {"app_mentions:read", "channels:history", "chat:write", "commands", "files:read"}


def _fail(msg: str, fix: str) -> None:
    print(f"✗ {msg}\n  fix: {fix}")
    sys.exit(1)


async def check(post: bool) -> None:
    from slack_sdk.errors import SlackApiError
    from slack_sdk.web.async_client import AsyncWebClient

    s = get_settings()
    if not s.slack_bot_token.startswith("xoxb-"):
        _fail(
            "SLACK_BOT_TOKEN is missing or not a bot token (xoxb-…)",
            "OAuth & Permissions → Bot User OAuth Token, paste into backend/.env",
        )
    if not s.slack_app_token.startswith("xapp-"):
        _fail(
            "SLACK_APP_TOKEN is missing or not an app-level token (xapp-…)",
            "Basic Information → App-Level Tokens → Generate (scope connections:write)",
        )

    bot = AsyncWebClient(token=s.slack_bot_token)
    try:
        auth = await bot.auth_test()
    except SlackApiError as exc:
        _fail(f"bot token rejected: {exc.response['error']}", "reinstall the app and copy the new token")
    scopes = set((auth.headers.get("x-oauth-scopes") or "").split(","))
    print(f"✓ bot token: @{auth['user']} in workspace {auth['team']}")
    missing = REQUIRED_SCOPES - scopes
    if missing:
        _fail(
            f"bot token lacks scopes: {', '.join(sorted(missing))}",
            "create the app from backend/slack_app/manifest.yml, or add the scopes and reinstall",
        )
    print("✓ scopes")

    app = AsyncWebClient(token=s.slack_app_token)
    try:
        await app.apps_connections_open(app_token=s.slack_app_token)
    except SlackApiError as exc:
        _fail(
            f"Socket Mode connection refused: {exc.response['error']}",
            "Settings → Socket Mode → enable; the app token needs connections:write",
        )
    print("✓ Socket Mode")

    for name, channel in (("SLACK_OPS_CHANNEL", s.slack_ops_channel), ("SLACK_DIGEST_CHANNEL", s.slack_digest_channel)):
        if not channel:
            print(f"· {name} not set (optional)")
            continue
        try:
            info = await bot.conversations_info(channel=channel)
        except SlackApiError as exc:
            _fail(
                f"{name}={channel}: {exc.response['error']}",
                "use the channel ID (channel details → bottom of the About tab), not its name",
            )
        if not info["channel"].get("is_member"):
            _fail(f"{name}: the bot is not in #{info['channel']['name']}", f"in Slack: /invite @{auth['user']}")
        print(f"✓ {name}: #{info['channel']['name']}")

    if post and s.slack_ops_channel:
        await bot.chat_postMessage(channel=s.slack_ops_channel, text="VenturesLab is connected ✅")
        print("✓ test message posted")
    print("\nAll good. Start the API (make api) and drop a PDF deck in a channel the bot is in.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", action="store_true", help="post a test message to SLACK_OPS_CHANNEL")
    asyncio.run(check(ap.parse_args().post))


if __name__ == "__main__":
    main()
