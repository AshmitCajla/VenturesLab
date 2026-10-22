"""Weekly digest: everything analysed in the last N days, plus service health."""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime

from core.config import get_settings
from services import cache, slack_client, slo
from services.report_text import red_flags
from services.runs import runs

logger = logging.getLogger("digest")


async def build(days: int = 7) -> tuple[str, list[dict]]:
    recent = await runs.list(limit=500, since=time.time() - days * 86400, with_result=True)
    recent = [r for r in recent if r.source not in ("eval", "redteam", "chaos")]
    done = [r for r in recent if r.status == "done"]
    verdicts = Counter(r.recommendation for r in done)
    top = sorted(done, key=lambda r: -(r.score or 0))[:5]
    flagged = [(r, red_flags(r.result or {}, limit=1)) for r in done]
    flagged = [(r, f[0]) for r, f in flagged if f][:5]
    health = await slo.status()
    cost = sum(r.cost_usd or 0 for r in recent)
    title = f"📊 VenturesLab weekly digest: {len(done)} venture(s) analysed"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
         f"🟢 BUILD {verdicts.get('BUILD', 0)} · 🟡 PIVOT {verdicts.get('PIVOT', 0)} · 🔴 PASS {verdicts.get('PASS', 0)}"
         f" · ❌ failed {sum(r.status == 'failed' for r in recent)}"}},
    ]
    if top:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Highest scores*\n" + "\n".join(
            f"• *{r.title or 'Untitled'}*: {r.recommendation} {r.score:.0%}" for r in top)}})
    if flagged:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Red flags worth a look*\n" + "\n".join(
            f"• *{r.title or 'Untitled'}*: {f}" for r, f in flagged)}})
    w = health["windows"]["1d"] if days <= 1 else health["windows"]["30d"]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                   f"Service: success {w['success_ratio'] if w['success_ratio'] is not None else 'n/a'} "
                   f"(target {health['objectives']['success_ratio']}) · error budget left "
                   f"{health['error_budget']['remaining_fraction']:.0%} · model spend ${cost:.3f}"}]})
    return title, blocks


async def send(days: int = 7) -> str | None:
    channel = get_settings().slack_digest_channel
    if not channel:
        return None
    title, blocks = await build(days)
    return await slack_client.post(channel, title, blocks)


async def scheduler(weekday: int = 0, hour: int = 9) -> None:
    """Send once a week (Monday 09:00 local by default). Survives restarts via the cache table."""
    import asyncio

    while True:
        now = datetime.now()
        week = now.strftime("%G-W%V")
        if now.weekday() == weekday and now.hour >= hour and not await cache.get("digest", week):
            try:
                await send()
                await cache.put("digest", week, True, ttl_s=8 * 86400)
            except Exception:
                logger.exception("digest failed")
        await asyncio.sleep(600)
