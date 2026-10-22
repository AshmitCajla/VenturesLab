"""Plain-text and Markdown renderings of a run, for Slack, MCP and the digest."""
from __future__ import annotations


def usd(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:,.0f}"


def ok(section) -> bool:
    return isinstance(section, dict) and section and "error" not in section and "skipped" not in section


def headline(result: dict) -> str:
    p = result.get("venture_profile") or {}
    return f"{p.get('name', 'Venture')}: {result.get('recommendation', 'n/a')} ({(result.get('score') or 0):.0%})"


def red_flags(result: dict, limit: int = 4) -> list[str]:
    flags = []
    for c in (result.get("claims") or {}).get("claims", []):
        if c.get("status") == "conflict":
            flags.append(f"Deck claims {c['value']} ({c['kind'].replace('_', ' ')}, slide {c['slide']}); "
                         f"independent sources: {c.get('external_estimate') or 'disagree'}")
        elif c.get("citation") == "citation_not_found":
            flags.append(f"Claim '{c['claim'][:80]}' could not be found on slide {c['slide']}")
    sec = result.get("security") or {}
    if sec.get("hidden_text") or sec.get("injections"):
        flags.append(f"Deck contained {sec.get('hidden_text', 0)} hidden passage(s) and "
                     f"{sec.get('injections', 0)} instruction(s) aimed at the AI; removed before analysis")
    low = [k for k, v in (result.get("confidence") or {}).items() if v.get("level") == "Low"]
    if low:
        flags.append("Thin evidence for: " + ", ".join(k.replace("_", " ") for k in low))
    return flags[:limit]


def to_markdown(result: dict, run_id: str | None = None) -> str:
    p = result.get("venture_profile") or {}
    lines = [f"# {headline(result)}", ""]
    if ok(p):
        lines += [f"**Problem:** {p['problem']}", f"**Solution:** {p['solution']}",
                  f"**Customer:** {p['customer']}", f"**Model:** {p['business_model']} · **Stage:** {p['stage']}", ""]
    sc = result.get("scoring") or {}
    if ok(sc):
        lines += [f"**Verdict:** {sc.get('verdict_rationale', '')}", "", "| criterion | score | why |", "|---|---|---|"]
        for k in sc.get("weights", {}):
            lines.append(f"| {k.replace('_', ' ')} | {sc[k]['score']}/10 | {sc[k]['reason']} |")
        lines.append("")
    ms = result.get("market_size") or {}
    if ok(ms):
        lines += [f"**Market:** TAM {usd(ms['tam_usd'])} · SAM {usd(ms['sam_usd'])} · SOM {usd(ms['som_usd'])}", ""]
    claims = (result.get("claims") or {}).get("claims", [])
    if claims:
        lines += ["**Deck claims checked:**"]
        for c in claims:
            lines.append(f"- {c['claim']} (slide {c['slide']}): {c['status']}"
                         + (f"; outside estimate {c['external_estimate']}" if c.get("external_estimate") else "")
                         + ("; citation not found" if c.get("citation") == "citation_not_found" else ""))
        lines.append("")
    flags = red_flags(result)
    if flags:
        lines += ["**Red flags:**", *[f"- {f}" for f in flags], ""]
    conf = result.get("confidence") or {}
    if conf:
        lines.append("**Confidence:** " + ", ".join(f"{k.replace('_', ' ')} {v['level']}" for k, v in conf.items()))
    cost = (result.get("cost") or {}).get("spent_usd")
    if cost is not None:
        lines.append(f"\n_Estimated model cost: ${cost:.4f}_" + (f" · run `{run_id}`" if run_id else ""))
    return "\n".join(lines)
