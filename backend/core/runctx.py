"""Per-run context carried through asyncio tasks with a ContextVar.

Holds the run id (shared by logs, traces, metrics and the Slack thread) and the
cost budget. LangGraph runs nodes in child tasks, which copy the context, and
``RunContext`` is mutable, so spending recorded in any agent is seen by all.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class RunContext:
    run_id: str
    budget_usd: float = 0.0  # 0 = unlimited
    spent_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def record(self, usage: dict) -> None:
        self.calls.append(usage)
        self.spent_usd += usage.get("cost_usd", 0.0)

    @property
    def remaining_usd(self) -> float | None:
        return None if not self.budget_usd else self.budget_usd - self.spent_usd

    def over_budget(self, reserve_usd: float = 0.0) -> bool:
        return bool(self.budget_usd) and self.spent_usd + reserve_usd > self.budget_usd

    def summary(self) -> dict:
        by_model: dict[str, dict] = {}
        for c in self.calls:
            m = by_model.setdefault(
                f"{c['provider']}:{c['model']}", {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
            )
            m["calls"] += 1
            m["tokens_in"] += c.get("tokens_in", 0)
            m["tokens_out"] += c.get("tokens_out", 0)
            m["cost_usd"] = round(m["cost_usd"] + c.get("cost_usd", 0.0), 6)
        return {
            "budget_usd": self.budget_usd,
            "spent_usd": round(self.spent_usd, 6),
            "llm_calls": len(self.calls),
            "by_model": by_model,
        }


_current: ContextVar[RunContext | None] = ContextVar("run_context", default=None)


def current() -> RunContext | None:
    return _current.get()


def current_run_id() -> str | None:
    ctx = _current.get()
    return ctx.run_id if ctx else None


def start(run_id: str, budget_usd: float = 0.0) -> RunContext:
    ctx = RunContext(run_id=run_id, budget_usd=budget_usd)
    _current.set(ctx)
    return ctx
