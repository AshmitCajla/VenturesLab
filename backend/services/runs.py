"""Run store: every analysis, its progress and its result, persisted in SQLite.

Replaces the v2 in-memory job dict, which lost everything on restart and so could
not support history, the Slack App Home, the weekly digest or SLO reporting.
Active runs are also kept in memory so polling does not hit the disk.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from services.db import get_db

JSON_FIELDS = ("input", "progress", "warnings", "result", "slack")


@dataclass
class Run:
    id: str
    status: str = "queued"           # queued | running | done | failed
    source: str = "api"              # api | slack | mcp | eval | chaos
    user_id: str | None = None
    title: str | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    score: float | None = None
    recommendation: str | None = None
    cost_usd: float | None = None
    error: str | None = None
    input: dict = field(default_factory=dict)
    progress: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    result: dict | None = None
    slack: dict | None = None

    @property
    def elapsed_s(self) -> float:
        return round((self.finished or time.time()) - self.created, 1)

    def public(self, include_result: bool = True) -> dict[str, Any]:
        out = {"job_id": self.id, "run_id": self.id, "status": self.status, "source": self.source,
               "title": self.title, "score": self.score, "recommendation": self.recommendation,
               "cost_usd": self.cost_usd, "progress": self.progress, "warnings": self.warnings,
               "error": self.error, "elapsed_s": self.elapsed_s, "created": self.created}
        if include_result:
            out["result"] = self.result
        return out

    def row(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        for k in JSON_FIELDS:
            d[k] = json.dumps(d[k], default=str) if d[k] is not None else None
        return d

    @classmethod
    def from_row(cls, row: dict) -> Run:
        for k in JSON_FIELDS:
            if row.get(k) is not None:
                row[k] = json.loads(row[k])
        row["input"] = row.get("input") or {}
        row["progress"] = row.get("progress") or []
        row["warnings"] = row.get("warnings") or []
        return cls(**row)


class RunStore:
    def __init__(self) -> None:
        self.active: dict[str, Run] = {}

    async def save(self, run: Run) -> None:
        row = run.row()
        cols = ", ".join(row)
        await get_db().execute(
            f"insert or replace into runs ({cols}) values ({', '.join(':' + k for k in row)})", row)
        if run.status in ("queued", "running"):
            self.active[run.id] = run
        else:
            self.active.pop(run.id, None)

    async def get(self, run_id: str) -> Run | None:
        if run_id in self.active:
            return self.active[run_id]
        row = await get_db().fetchone("select * from runs where id = ?", (run_id,))
        return Run.from_row(row) if row else None

    async def list(self, user_id: str | None = None, limit: int = 20, since: float | None = None,
                   status: str | None = None, with_result: bool = False) -> list[Run]:
        cols = "*" if with_result else ", ".join(c for c in Run.__dataclass_fields__ if c != "result")
        where, params = [], []
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if since:
            where.append("created >= ?")
            params.append(since)
        if status:
            where.append("status = ?")
            params.append(status)
        sql = f"select {cols} from runs" + (" where " + " and ".join(where) if where else "")
        rows = await get_db().fetchall(sql + " order by created desc limit ?", (*params, limit))
        return [Run.from_row(r) for r in rows]

    async def mark_interrupted(self) -> int:
        """Runs left queued/running by a crash or restart are failed explicitly, not left hanging."""
        rows = await get_db().fetchall("select id from runs where status in ('queued', 'running')")
        await get_db().execute(
            "update runs set status = 'failed', error = 'interrupted by a server restart', finished = ? "
            "where status in ('queued', 'running')", (time.time(),))
        return len(rows)

    async def stats(self, since: float) -> dict:
        rows = await get_db().fetchall(
            "select status, count(*) n, avg(cost_usd) cost, avg(finished - created) secs from runs "
            "where created >= ? group by status", (since,))
        fb = await get_db().fetchall(
            "select rating, count(*) n from feedback where created >= ? group by rating", (since,))
        return {"by_status": {r["status"]: {"runs": r["n"], "avg_cost_usd": r["cost"], "avg_s": r["secs"]}
                              for r in rows},
                "feedback": {r["rating"]: r["n"] for r in fb}}

    async def add_feedback(self, run_id: str, rating: str, user_id: str | None = None,
                           section: str | None = None, comment: str | None = None) -> None:
        await get_db().execute(
            "insert into feedback (run_id, user_id, rating, section, comment, created) values (?, ?, ?, ?, ?, ?)",
            (run_id, user_id, rating, section, comment, time.time()))

    async def corrections(self, limit: int = 200) -> list[dict]:
        return await get_db().fetchall(
            "select f.*, r.title, r.input from feedback f join runs r on r.id = f.run_id "
            "where f.rating = 'correction' order by f.created desc limit ?", (limit,))


runs = RunStore()
