"""Offline evaluation harness.

Runs the full 12-agent pipeline over ``cases.json`` and reports, per agent:
latency, self-correction retries, failures and quality-check pass rate, plus
whether each verdict falls in the range a human reviewer expected.

    cd backend && python -m evaluation.run_eval            # real LLMs (needs keys)
    cd backend && python -m evaluation.run_eval --fake     # plumbing check, no keys
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


async def evaluate(cases: list[dict]) -> dict:
    from workflow.graph import compiled_graph

    per_agent = defaultdict(lambda: {"ms": [], "attempts": [], "errors": 0, "checks": [0, 0]})
    rows = []
    for case in cases:
        t0 = time.perf_counter()
        out = await compiled_graph.ainvoke({"session_id": case["id"], "idea": case["idea"],
                                            "trace": [], "evaluations": []})
        wall = time.perf_counter() - t0
        for t in out.get("trace", []):
            a = per_agent[t["agent"]]
            if t["status"] == "ok":
                a["ms"].append(t["ms"])
                a["attempts"].append(t["attempts"])
                a["checks"][0] += t.get("checks_passed", 0)
                a["checks"][1] += t.get("checks_total", 0)
            else:
                a["errors"] += 1
        evals = out.get("evaluations", [])
        rows.append({
            "case": case["id"], "score": out.get("score"), "recommendation": out.get("recommendation"),
            "expected": case["expected"], "verdict_ok": out.get("recommendation") in case["expected"],
            "checks_passed": sum(e["passed"] for e in evals), "checks_total": len(evals),
            "wall_s": round(wall, 1),
        })
    return {"cases": rows, "agents": per_agent}


def to_markdown(res: dict) -> str:
    rows, agents = res["cases"], res["agents"]
    lines = ["# VenturesLab evaluation report", "",
             f"Cases: {len(rows)} | verdicts in expected range: "
             f"{sum(r['verdict_ok'] for r in rows)}/{len(rows)} | median end-to-end: "
             f"{statistics.median(r['wall_s'] for r in rows):.1f}s", "",
             "| case | score | verdict | expected | checks | time (s) |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['case']} | {r['score']} | {r['recommendation']} | {'/'.join(r['expected'])} "
                     f"| {r['checks_passed']}/{r['checks_total']} | {r['wall_s']} |")
    lines += ["", "| agent | runs | median ms | self-corrections | errors | check pass rate |",
              "|---|---|---|---|---|---|"]
    for name, a in agents.items():
        runs = len(a["ms"])
        med = statistics.median(a["ms"]) if runs else 0
        retries = sum(x - 1 for x in a["attempts"])
        rate = f"{a['checks'][0] / a['checks'][1]:.0%}" if a["checks"][1] else "n/a"
        lines.append(f"| {name} | {runs} | {med:.0f} | {retries} | {a['errors']} | {rate} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="use the deterministic fake LLM")
    ap.add_argument("--out", default=str(HERE / "report.md"))
    args = ap.parse_args()
    if args.fake:
        import os
        os.environ.setdefault("EMBEDDINGS", "hash")
        os.environ.setdefault("WEB_SEARCH", "0")
        from core import model_router
        from tests.fakes import FakeLLM
        llm = FakeLLM()
        model_router.set_llm_factory(lambda role: llm)
    cases = json.loads((HERE / "cases.json").read_text())
    res = asyncio.run(evaluate(cases))
    md = to_markdown(res)
    Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
