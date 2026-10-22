"""Quality gate for pull requests: block merges that make measured quality worse.

Runs the deterministic, key-free evaluations and compares them with
``evaluation/data/baseline.json``:

* red-team attack success with defences on (instruction and evidence families),
* false positives of the injection detector on ``data/benign_lines.txt``,
* chaos experiments: does the top error signal name the injected fault?
* offline pipeline: share of quality checks passing.

Exits 1 on any regression and writes a Markdown summary (used as the CI job
summary). ``--update-baseline`` accepts the current numbers, for intended changes.
Live-model quality (golden set, consistency) runs in the manual "live-eval" workflow,
because it needs API keys and costs money.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from evaluation.paths import DATA  # noqa: E402


def benign_false_positives() -> list[str]:
    from services.security import injection_reason

    lines = [
        ln.strip()
        for ln in (DATA / "benign_lines.txt").read_text().splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    return [ln for ln in lines if injection_reason(ln)]


async def measure() -> dict:
    from evaluation import chaos, redteam
    from evaluation.golden import use_fake

    results = await redteam.evaluate(redteam.ATTACKS, live=False)
    instr, evid = redteam.asr(results, "on", "instruction"), redteam.asr(results, "on", "evidence")

    use_fake()
    os.environ["CHAOS"] = "1"
    os.environ["WEB_SEARCH"] = "1"
    from core.config import get_settings
    from services import web_research

    get_settings.cache_clear()
    web_research._search_sync = lambda q, n: [{"title": "Report", "body": "Market", "href": "https://example.com"}]
    rows = [await chaos.run_experiment(e, 2, live=False) for e in chaos.EXPERIMENTS]
    os.environ["CHAOS"] = "0"
    get_settings.cache_clear()

    from evaluation.run_eval import evaluate as offline_eval

    cases = json.loads((DATA / "cases.json").read_text())
    res = await offline_eval(cases)
    passed = sum(r["checks_passed"] for r in res["cases"])
    total = sum(r["checks_total"] for r in res["cases"])
    fps = benign_false_positives()
    return {
        "redteam_instruction_asr_on": list(instr),
        "redteam_evidence_asr_on": list(evid),
        "benign_false_positives": len(fps),
        "benign_false_positive_lines": fps,
        "chaos_signal_accuracy": round(sum(r["signal_ok"] for r in rows) / len(rows), 3),
        "chaos_failed_experiments": [r["experiment"] for r in rows if not r["signal_ok"]],
        "offline_check_pass_rate": round(passed / total, 3) if total else None,
    }


def compare(now: dict, base: dict) -> list[str]:
    problems = []
    for key in ("redteam_instruction_asr_on", "redteam_evidence_asr_on"):
        (s, n), (bs, bn) = now[key], base[key]
        if n and bn and s / n > bs / bn + 1e-9:
            problems.append(f"{key}: {s}/{n} attacks succeed with defences on (baseline {bs}/{bn})")
    if now["benign_false_positives"] > base["benign_false_positives"]:
        problems.append(f"injection detector flags ordinary copy: {now['benign_false_positive_lines']}")
    if now["chaos_signal_accuracy"] < base["chaos_signal_accuracy"]:
        problems.append(f"chaos diagnosis signals regressed: {now['chaos_failed_experiments']}")
    if (now["offline_check_pass_rate"] or 0) < base["offline_check_pass_rate"]:
        problems.append(
            f"offline quality-check pass rate {now['offline_check_pass_rate']} "
            f"< baseline {base['offline_check_pass_rate']}"
        )
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--summary", help="write a Markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    args = ap.parse_args()
    os.environ.setdefault("DB_PATH", ":memory:")
    os.environ.setdefault("LOG_DIR", "")
    os.environ.setdefault("EMBEDDINGS", "hash")
    os.environ.setdefault("VISION_MODEL", "off")
    now = asyncio.run(measure())
    base_path = DATA / "baseline.json"
    base = json.loads(base_path.read_text())
    if args.update_baseline:
        base_path.write_text(json.dumps({k: now[k] for k in base}, indent=1) + "\n")
        print("baseline updated")
        return
    problems = compare(now, base)
    lines = ["## Quality gate", "", "| metric | now | baseline |", "|---|---|---|"]
    lines += [f"| {k} | {now[k]} | {base[k]} |" for k in base]
    lines += ["", "**Regressions:**" if problems else "**No regressions.**", *[f"- {p}" for p in problems]]
    md = "\n".join(lines) + "\n"
    print(md)
    if args.summary:
        with Path(args.summary).open("a") as f:
            f.write(md)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
