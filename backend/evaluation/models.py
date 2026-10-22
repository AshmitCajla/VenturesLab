"""Cost vs. quality across model configurations.

Each configuration sets the fast and reasoning models, runs the golden set and
the consistency check, and reports cost per run, time per run, verdict accuracy,
key-number recall and score standard deviation side by side.

    cd backend && python -m evaluation.models --repeats 2
    cd backend && python -m evaluation.models --configs groq-oss-small groq-oss-split
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

CONFIGS = {
    "groq-oss-split": {"LLM_PROVIDER": "groq", "FAST_MODEL": "openai/gpt-oss-20b", "REASONING_MODEL": "openai/gpt-oss-120b"},
    "groq-oss-small": {"LLM_PROVIDER": "groq", "FAST_MODEL": "openai/gpt-oss-20b", "REASONING_MODEL": "openai/gpt-oss-20b"},
    "groq-oss-large": {"LLM_PROVIDER": "groq", "FAST_MODEL": "openai/gpt-oss-120b", "REASONING_MODEL": "openai/gpt-oss-120b"},
    "groq-qwen": {"LLM_PROVIDER": "groq", "FAST_MODEL": "qwen/qwen3.8-27b", "REASONING_MODEL": "qwen/qwen3.8-27b"},
    "openrouter-free": {"LLM_PROVIDER": "openrouter"},
}


async def run_config(name: str, env: dict, repeats: int) -> dict:
    from core.config import get_settings
    from evaluation.consistency import measure
    from evaluation.golden import evaluate, load_cases, summarize

    os.environ.update(env)
    get_settings.cache_clear()
    cases = load_cases()
    golden = summarize(await evaluate(cases, source="eval"))
    cons = await measure(cases[:2], repeats) if repeats > 1 else []
    sds = [r["score_sd"] for r in cons if r["score_sd"] is not None]
    return {"config": name, **env, **golden, "score_sd": round(statistics.mean(sds), 3) if sds else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="*", default=list(CONFIGS))
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--out", default=str(HERE / "models_report.md"))
    args = ap.parse_args()
    rows = []
    for name in args.configs:
        rows.append(asyncio.run(run_config(name, CONFIGS[name], args.repeats)))
        print(json.dumps(rows[-1]), file=sys.stderr)
    lines = ["# Cost vs. quality by model configuration", "",
             "| config | fast | reasoning | verdict accuracy | key-number recall | red-flag recall | score sd "
             "| cost per run | time per run (s) |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['config']} | {r.get('FAST_MODEL', 'default')} | {r.get('REASONING_MODEL', 'default')} "
                     f"| {r['verdict_accuracy']} | {r['key_number_recall']} | {r['red_flag_recall']} | {r['score_sd']} "
                     f"| ${r['mean_cost_usd'] or 0:.4f} | {r['mean_wall_s']} |")
    Path(args.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
