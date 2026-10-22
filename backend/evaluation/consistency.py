"""Consistency: run the same input N times and measure how much the answer moves.

Reports, per case: score mean and standard deviation, verdict flip rate (share
of runs whose verdict differs from the majority), and the standard deviation of
each criterion rating. A model that gives BUILD on Monday and PASS on Tuesday
for the same deck is not usable, whatever its average accuracy.

    cd backend && python -m evaluation.consistency --repeats 5            # real models
    cd backend && python -m evaluation.consistency --repeats 3 --fake     # plumbing
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import uuid
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


async def measure(cases: list[dict], repeats: int) -> list[dict]:
    from schema import SCORING_WEIGHTS
    from services.runs import Run
    from workflow.runner import execute

    rows = []
    for case in cases:
        results = []
        for _ in range(repeats):
            run = await execute(Run(id=str(uuid.uuid4()), source="eval", input={"idea": case.get("idea", "")}),
                                case.get("pdf"))
            if run.status == "done" and run.result.get("score") is not None:
                results.append(run.result)
        scores = [r["score"] for r in results]
        verdicts = Counter(r["recommendation"] for r in results)
        majority = verdicts.most_common(1)[0][0] if verdicts else None
        crit_sd = {}
        for k in SCORING_WEIGHTS:
            vals = [r["scoring"][k]["score"] for r in results if isinstance(r.get("scoring", {}).get(k), dict)]
            crit_sd[k] = round(statistics.pstdev(vals), 2) if len(vals) > 1 else None
        rows.append({
            "case": case["id"], "runs": len(results), "requested": repeats,
            "score_mean": round(statistics.mean(scores), 3) if scores else None,
            "score_sd": round(statistics.pstdev(scores), 3) if len(scores) > 1 else None,
            "verdicts": dict(verdicts), "flip_rate": round(1 - verdicts[majority] / len(results), 2) if results else None,
            "criterion_sd": crit_sd,
        })
        print(json.dumps(rows[-1]), file=sys.stderr)
    return rows


def to_markdown(rows: list[dict], mode: str) -> str:
    sds = [r["score_sd"] for r in rows if r["score_sd"] is not None]
    flips = [r["flip_rate"] for r in rows if r["flip_rate"] is not None]
    lines = ["# Consistency report", "", f"Mode: {mode}", "",
             f"Mean score sd {statistics.mean(sds):.3f} · mean verdict flip rate {statistics.mean(flips):.2f}"
             if sds and flips else "Not enough complete runs to measure.", "",
             "| case | runs | score mean | score sd | verdicts | flip rate | least stable criterion |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        crit = {k: v for k, v in r["criterion_sd"].items() if v is not None}
        worst = max(crit, key=crit.get) if crit else None
        lines.append(f"| {r['case']} | {r['runs']}/{r['requested']} | {r['score_mean']} | {r['score_sd']} "
                     f"| {', '.join(f'{k} {v}' for k, v in r['verdicts'].items())} | {r['flip_rate']} "
                     f"| {f'{worst} (sd {crit[worst]})' if worst else '—'} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    from evaluation.golden import load_cases, use_fake

    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--cases", nargs="*", help="case ids (golden set and evaluation/cases.json)")
    ap.add_argument("--out", default=str(HERE / "consistency_report.md"))
    args = ap.parse_args()
    if args.fake:
        use_fake()
    cases = load_cases() + json.loads((HERE / "cases.json").read_text())
    if args.cases:
        cases = [c for c in cases if c["id"] in args.cases]
    rows = asyncio.run(measure(cases, args.repeats))
    md = to_markdown(rows, "fake models (plumbing only)" if args.fake else "live models")
    Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
