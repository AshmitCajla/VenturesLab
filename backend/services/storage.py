"""Persist finished analyses: Supabase when configured, local JSON otherwise.

Supabase table (run once in the SQL editor):

    create table if not exists analyses (
      id uuid primary key,
      created_at timestamptz default now(),
      idea text,
      score double precision,
      recommendation text,
      result jsonb
    );
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from core.config import get_settings

logger = logging.getLogger(__name__)


def _save_local(session_id: str, payload: dict) -> str:
    folder = Path(get_settings().results_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{session_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return str(path)


def _save_supabase(session_id: str, payload: dict) -> str:
    from supabase import create_client

    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_key)
    client.table("analyses").upsert({
        "id": session_id,
        "idea": (payload.get("idea") or "")[:2000],
        "score": payload.get("score"),
        "recommendation": payload.get("recommendation"),
        "result": json.loads(json.dumps(payload, default=str)),
    }).execute()
    return f"supabase:analyses/{session_id}"


async def save_analysis(session_id: str, payload: dict) -> str | None:
    s = get_settings()
    try:
        if s.supabase_url and s.supabase_key:
            return await asyncio.to_thread(_save_supabase, session_id, payload)
        return await asyncio.to_thread(_save_local, session_id, payload)
    except Exception as exc:  # persistence must never fail the analysis
        logger.warning("could not persist analysis %s: %s", session_id, exc)
        return None
