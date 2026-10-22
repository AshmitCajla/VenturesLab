"""SQLite persistence shared by the run store, the cache and cross-deck memory.

One file (DB_PATH), WAL mode, one connection guarded by a lock and used from a
worker thread so the event loop never blocks on disk. SQLite is enough for a
single API instance; the store interfaces are small so Postgres can replace it.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from core.config import get_settings

T = TypeVar("T")

SCHEMA = """
create table if not exists runs (
  id text primary key,
  status text not null,
  source text not null default 'api',
  user_id text,
  title text,
  created real not null,
  started real,
  finished real,
  score real,
  recommendation text,
  cost_usd real,
  error text,
  input json,
  progress json,
  warnings json,
  result json,
  slack json
);
create index if not exists runs_created on runs(created);
create index if not exists runs_user on runs(user_id, created);

create table if not exists feedback (
  id integer primary key autoincrement,
  run_id text not null,
  user_id text,
  rating text not null,            -- up | down | correction
  section text,
  comment text,
  created real not null
);

create table if not exists cache (
  key text primary key,
  value json not null,
  expires real not null
);

create table if not exists memory (
  run_id text primary key,
  company text,
  company_key text,
  created real not null,
  embedding blob not null,
  profile json not null,
  snapshot json not null
);
create index if not exists memory_company on memory(company_key);

create table if not exists incidents (
  id text primary key,
  alert text not null,
  status text not null,            -- firing | resolved
  started real not null,
  resolved real,
  labels json,
  diagnosis json,
  postmortem text,
  slack json
);
"""


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("pragma journal_mode=wal")
            self._conn.executescript(SCHEMA)

    def run_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        with self._lock:
            return fn(self._conn)

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self.run_sync, fn)

    async def execute(self, sql: str, params: tuple | dict = ()) -> None:
        await self.run(lambda c: c.execute(sql, params))

    async def fetchall(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        return await self.run(lambda c: [dict(r) for r in c.execute(sql, params).fetchall()])

    async def fetchone(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(get_settings().db_path)
    return _db


def reset_db(path: str | None = None) -> Database:
    """Point the process at a new database (tests)."""
    global _db
    _db = Database(path or get_settings().db_path)
    return _db
