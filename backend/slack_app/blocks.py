"""Block Kit layouts for the Slack bot: live progress, summary card, App Home, digest, modals."""
from __future__ import annotations

import time

from core.config import get_settings
from services.report_text import red_flags, usd
from services.runs import Run

EMOJI = {"ok": "✅", "error": "⚠️", "skipped": "⏭️"}
VERDICT = {"BUILD": "🟢", "PIVOT": "🟡", "PASS": "🔴"}
SECTIONS = ["venture_profile", "market_analysis", "competitors", "customer_validation", "market_size",
            "scoring", "claims", "swot", "pestle", "porter", "roadmap", "gtm_strategy", "financial_model"]


def report_url(run_id: str) -> str:
    return f"{get_settings().public_ui_url.rstrip('/')}/?run={run_id}"


def progress(run: Run, total_agents: int) -> tuple[str, list[dict]]:
    agents = [p for p in run.progress if "number" in p]
    done = len(agents)
    bar = "▰" * round(10 * done / max(1, total_agents)) + "▱" * (10 - round(10 * done / max(1, total_agents)))
    if run.status == "done":
        head = f"✅ Analysis complete in {run.elapsed_s:.0f}s"
    elif run.status == "failed":
        head = f"❌ Analysis failed: {run.error}"
    elif run.status == "queued":
        head = "⏳ Queued…"
    else:
        head = f"⏳ Analysing {run.title or 'venture'}… {bar} {done}/{total_agents}"
    lines = [f"{EMOJI.get(p.get('status', 'ok'), '•')} {p['name']}"
             + (f" · {p['ms'] / 1000:.1f}s" if p.get("ms") else "")
             + (" · self-corrected" if p.get("attempts", 1) > 1 else "")
             + (f" · _{p['error'][:80]}_" if p.get("status") == "error" else "")
             for p in run.progress[-14:]]
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{head}*"}}]
    if lines and run.status != "done":
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "\n".join(lines)[:2900]}]})
    for w in run.warnings[-3:]:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"⚠️ {w[:300]}"}]})
    return head, blocks


def summary_card(run: Run) -> tuple[str, list[dict]]:
    r = run.result or {}
    p = r.get("venture_profile") or {}
    rec, score = r.get("recommendation", "n/a"), r.get("score") or 0
    text = f"{p.get('name', 'Venture')}: {rec} ({score:.0%})"
    sc = r.get("scoring") or {}
    crit = [(k, sc[k]) for k in sc.get("weights", {}) if isinstance(sc.get(k), dict)]
    crit_txt = "  ".join(f"{k.replace('_', ' ')} *{v['score']}*/10" for k, v in crit)
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{VERDICT.get(rec, '⚪')} {text}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (sc.get("verdict_rationale") or p.get("problem", ""))[:2900]}},
    ]
    if crit_txt:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": crit_txt}]})
    ms = r.get("market_size") or {}
    if "tam_usd" in ms:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                       f"TAM {usd(ms['tam_usd'])} · SAM {usd(ms['sam_usd'])} · SOM {usd(ms['som_usd'])}"}]})
    flags = red_flags(r)
    if flags:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*🚩 Red flags*\n" + "\n".join(
            f"• {f}" for f in flags)}})
    hist = r.get("history") or {}
    if hist.get("previous_version"):
        ch = hist["previous_version"]["changes"]
        bits = []
        if "score" in ch:
            bits.append(f"score {ch['score']['from']:.0%} → {ch['score']['to']:.0%}")
        if "recommendation" in ch:
            bits.append(f"verdict {ch['recommendation']['from']} → {ch['recommendation']['to']}")
        if "claims" in ch:
            bits.append(f"{sum(len(v) for v in ch['claims'].values())} claim change(s)")
        lead = ("🔁 Same deck as last time; differences are run-to-run model variance: " if ch.get("same_deck")
                else "🔁 Updated deck since the last version: ")
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                       lead + (", ".join(bits) or "no material changes")}]})
    if hist.get("similar"):
        names = ", ".join(f"{s['company']} ({s['recommendation']})" for s in hist["similar"])
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"🧭 Similar to past decks: {names}"}]})
    cost = (r.get("cost") or {}).get("spent_usd")
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
                   f"Run `{run.id[:8]}` · {run.elapsed_s:.0f}s" + (f" · ${cost:.4f}" if cost is not None else "")
                   + " · reply in this thread to ask about the report"}]})
    blocks.append({"type": "actions", "block_id": f"run:{run.id}", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "📄 Full report"}, "url": report_url(run.id),
         "action_id": "open_report"},
        {"type": "button", "text": {"type": "plain_text", "text": "🔄 Re-run"}, "action_id": "rerun", "value": run.id},
        {"type": "button", "text": {"type": "plain_text", "text": "👍"}, "action_id": "feedback_up", "value": run.id},
        {"type": "button", "text": {"type": "plain_text", "text": "👎"}, "action_id": "feedback_down", "value": run.id},
        {"type": "button", "text": {"type": "plain_text", "text": "✏️ Correct a section"},
         "action_id": "correct", "value": run.id},
    ]})
    return text, blocks


def correction_modal(run_id: str, thread: dict) -> dict:
    return {
        "type": "modal", "callback_id": "correction",
        "private_metadata": f"{run_id}|{thread.get('channel', '')}|{thread.get('thread_ts', '')}",
        "title": {"type": "plain_text", "text": "Correct the report"},
        "submit": {"type": "plain_text", "text": "Save"},
        "blocks": [
            {"type": "input", "block_id": "section", "label": {"type": "plain_text", "text": "Section"},
             "element": {"type": "static_select", "action_id": "value", "options": [
                 {"text": {"type": "plain_text", "text": s.replace("_", " ")}, "value": s} for s in SECTIONS]}},
            {"type": "input", "block_id": "comment", "label": {"type": "plain_text", "text": "What is wrong, and what is right?"},
             "element": {"type": "plain_text_input", "action_id": "value", "multiline": True}},
        ],
    }


def app_home(runs: list[Run], stats: dict) -> dict:
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "📈 VenturesLab"}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
         "Drop a pitch-deck PDF in any channel I'm in (or mention me with a website or description). "
         "I reply in a thread with a scored analysis, its sources and red flags. Ask follow-ups in the thread."}},
        {"type": "divider"},
        {"type": "header", "text": {"type": "plain_text", "text": "Your recent runs"}},
    ]
    if not runs:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "_No runs yet._"}})
    for r in runs[:10]:
        when = time.strftime("%b %d %H:%M", time.localtime(r.created))
        status = {"done": VERDICT.get(r.recommendation or "", "✅"), "failed": "❌"}.get(r.status, "⏳")
        detail = f"{r.recommendation} · {r.score:.0%}" if r.status == "done" and r.score is not None else r.status
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
                       f"{status} *{r.title or r.input.get('filename') or 'Untitled'}*  ·  {detail}  ·  {when}"},
                       **({"accessory": {"type": "button", "text": {"type": "plain_text", "text": "Open"},
                                         "url": report_url(r.id), "action_id": f"home_open_{r.id[:8]}"}}
                          if r.status == "done" else {})})
    by = stats.get("by_status", {})
    total = sum(v["runs"] for v in by.values())
    blocks += [{"type": "divider"}, {"type": "context", "elements": [{"type": "mrkdwn", "text":
               f"Last 7 days, whole workspace: {total} runs · {by.get('done', {}).get('runs', 0)} done · "
               f"{by.get('failed', {}).get('runs', 0)} failed · 👍 {stats.get('feedback', {}).get('up', 0)} "
               f"👎 {stats.get('feedback', {}).get('down', 0)}"}]}]
    return {"type": "home", "blocks": blocks}
