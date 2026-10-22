"""LLM judge for free-text quality, and the checks that decide whether to trust it.

The judge rates a report section 1-5 on specificity, grounding in the evidence
and usefulness to an investor. Its numbers are only reported as trusted after
calibration: it re-rates sections a human already rated
(``golden/judge_labels.json``) and we measure agreement. Below
``MIN_LABELS`` labels, or below ``MIN_KAPPA`` weighted kappa, the report says
the judge is uncalibrated and its scores must not be used to gate anything.

Analyst corrections from Slack are the other half of the loop:
``--export-corrections`` writes them to ``golden/corrections.jsonl``; ``--check-corrections``
re-runs each corrected input and asks the judge whether the new section still
contains the error the analyst described.

    python -m evaluation.judge --calibrate
    python -m evaluation.judge --export-corrections
    python -m evaluation.judge --check-corrections
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"
sys.path.insert(0, str(HERE.parent))

MIN_LABELS = 20
MIN_KAPPA = 0.6


class Rating(BaseModel):
    specificity: int = Field(ge=1, le=5, description="1 = generic boilerplate, 5 = specific to this venture")
    grounding: int = Field(ge=1, le=5, description="1 = unsupported, 5 = every claim follows from the evidence")
    usefulness: int = Field(ge=1, le=5, description="1 = useless to an investor, 5 = changes the decision")
    overall: int = Field(ge=1, le=5)
    reason: str


class StillWrong(BaseModel):
    still_contains_error: bool
    explanation: str


async def rate(section: str, output, evidence: dict) -> Rating:
    from core.llm import call_structured
    from services.security import fence

    prompt = f"""You are a strict reviewer of venture-capital analysis. Rate this section of an automated
report from 1 to 5 on each dimension. Judge only against the evidence shown.

Section: {section}
Output: {fence('section output', json.dumps(output, default=str)[:6000])}
Evidence the analyst had: {fence('evidence', json.dumps(evidence, default=str)[:6000])}"""
    r, _ = await call_structured("reasoning", Rating, prompt, "Judge", attempts=3)
    return r


def weighted_kappa(a: list[int], b: list[int], k: int = 5) -> float:
    """Quadratic-weighted Cohen's kappa for two raters on a 1..k scale."""
    import numpy as np

    n = len(a)
    if n == 0:
        return float("nan")
    obs = np.zeros((k, k))
    for x, y in zip(a, b, strict=True):
        obs[x - 1, y - 1] += 1
    ha, hb = obs.sum(1), obs.sum(0)
    exp = np.outer(ha, hb) / n
    w = np.array([[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)])
    denom = (w * exp).sum()
    return float(1 - (w * obs).sum() / denom) if denom else 1.0


async def calibrate() -> dict:
    path = GOLDEN / "judge_labels.json"
    labels = json.loads(path.read_text()) if path.exists() else []
    human, model = [], []
    for item in labels:
        r = await rate(item["section"], item["output"], item.get("evidence", {}))
        human.append(item["human_overall"])
        model.append(r.overall)
    exact = sum(h == m for h, m in zip(human, model, strict=True)) / len(human) if human else None
    within1 = sum(abs(h - m) <= 1 for h, m in zip(human, model, strict=True)) / len(human) if human else None
    kappa = weighted_kappa(human, model) if human else None
    trusted = bool(human) and len(human) >= MIN_LABELS and kappa is not None and kappa >= MIN_KAPPA
    return {"labels": len(human), "exact_agreement": exact, "within_one": within1, "weighted_kappa": kappa,
            "trusted": trusted,
            "verdict": "trusted" if trusted else
            f"UNCALIBRATED: need >= {MIN_LABELS} human labels and kappa >= {MIN_KAPPA} before using judge scores"}


async def export_corrections() -> int:
    from services.runs import runs

    rows = await runs.corrections(limit=10_000)
    GOLDEN.mkdir(exist_ok=True)
    with (GOLDEN / "corrections.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps({"run_id": r["run_id"], "section": r["section"], "correction": r["comment"],
                                "input": json.loads(r["input"]) if r["input"] else {}, "title": r["title"]}) + "\n")
    return len(rows)


async def check_corrections() -> list[dict]:
    from core.llm import call_structured
    from services.runs import Run
    from workflow.runner import execute

    path = GOLDEN / "corrections.jsonl"
    out = []
    for line in path.read_text().splitlines() if path.exists() else []:
        c = json.loads(line)
        if not c["input"].get("idea") and not c["input"].get("url"):
            out.append({**c, "result": "skipped: deck not stored with the correction"})
            continue
        run = await execute(Run(id=str(uuid.uuid4()), source="eval", input=c["input"]))
        section = (run.result or {}).get(c["section"])
        verdict, _ = await call_structured("reasoning", StillWrong, f"""An analyst said this section was wrong:
"{c['correction']}". Here is the section regenerated today: {json.dumps(section, default=str)[:6000]}.
Does it still contain the error the analyst described?""", "Correction check", attempts=3)
        out.append({"run_id": c["run_id"], "section": c["section"], "fixed": not verdict.still_contains_error,
                    "explanation": verdict.explanation})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--export-corrections", action="store_true")
    ap.add_argument("--check-corrections", action="store_true")
    args = ap.parse_args()
    if args.calibrate:
        print(json.dumps(asyncio.run(calibrate()), indent=1))
    if args.export_corrections:
        print(f"exported {asyncio.run(export_corrections())} correction(s) to {GOLDEN / 'corrections.jsonl'}")
    if args.check_corrections:
        print(json.dumps(asyncio.run(check_corrections()), indent=1))


if __name__ == "__main__":
    main()
