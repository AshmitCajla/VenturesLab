"""VenturesLab - Streamlit front end.

Talks to the FastAPI backend (BACKEND_URL). Submits a run, polls it while each
agent finishes, then shows the report with its sources, confidence, checked
claims and anything that was removed from a manipulated deck. ``?run=<id>``
opens a stored run directly (the Slack card's "Full report" button links here).
"""
from __future__ import annotations

import os
import time

import requests
import streamlit as st

from report import build_pdf, ok, usd


def _secret(name: str, default: str) -> str:
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except Exception:  # no secrets.toml
        return os.getenv(name, default)


BACKEND_URL = _secret("BACKEND_URL", "http://localhost:8000").rstrip("/")
GRAFANA_URL = _secret("GRAFANA_URL", "").rstrip("/")

st.set_page_config(page_title="VenturesLab", page_icon="📈", layout="wide")

LEVEL = {"High": "🟢", "Medium": "🟡", "Low": "🔴", "Unavailable": "⚪"}
STATUS = {"consistent": "✅ consistent", "conflict": "❌ conflict", "unverifiable": "➖ unverifiable"}


def md(text) -> str:
    """Escape '$' so Streamlit does not render money as LaTeX."""
    return str(text).replace("$", r"\$")


def describe_changes(ch: dict) -> str:
    """One readable line for the diff against the previous version of the same company's deck."""
    parts = []
    if "score" in ch:
        parts.append(f"score {ch['score']['from']:.1%} → {ch['score']['to']:.1%}")
    if "recommendation" in ch:
        parts.append(f"verdict {ch['recommendation']['from']} → {ch['recommendation']['to']}")
    if "stage" in ch:
        parts.append(f"stage {ch['stage']['from']} → {ch['stage']['to']}")
    for k, v in (ch.get("criteria") or {}).items():
        parts.append(f"{k.replace('_', ' ')} {v['from']} → {v['to']}")
    if "market_size" in ch and ch["market_size"].get("from"):
        parts.append(f"TAM {usd(ch['market_size']['from'].get('tam_usd'))} → {usd(ch['market_size']['to'].get('tam_usd'))}")
    if "claims" in ch:
        c = ch["claims"]
        parts.append(f"claims: {len(c['added'])} new, {len(c['removed'])} dropped, {len(c['changed'])} changed")
    return md("; ".join(parts) or "no material changes")


def api(method: str, path: str, **kw):
    return requests.request(method, f"{BACKEND_URL}{path}", timeout=kw.pop("timeout", 30), **kw)


@st.cache_data(ttl=3600)
def agent_list():
    try:
        return api("GET", "/agents", timeout=15).json()
    except Exception:
        return []


def confidence_badge(data: dict, key: str) -> None:
    c = (data.get("confidence") or {}).get(key)
    if c:
        st.caption(f"{LEVEL.get(c['level'], '')} Confidence: **{c['level']}**"
                   + (f" · {md('; '.join(c['reasons']))}" if c.get("reasons") else ""))


def sources(data: dict, key: str) -> None:
    ev = (data.get("evidence") or {}).get(key) or {}
    deck, web = ev.get("deck") or [], ev.get("web") or []
    if not (deck or web):
        return
    with st.expander(f"Sources ({len(deck)} slide passage(s), {len(web)} web)"):
        for d in deck:
            st.markdown(f"- **Slide {d.get('slide')}**"
                        + (f" (relevance {d['score']:.2f})" if d.get("score") is not None else "")
                        + f": {md(d.get('excerpt', ''))}…")
        for w in web:
            st.markdown(f"- [{md(w.get('title') or w.get('url'))}]({w.get('url')})")


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📈 VenturesLab")
    st.caption("AI analysts review a startup the way a VC fund would, and show their sources.")
    idea = st.text_area("Startup description", height=120,
                        placeholder="What problem, for whom, how you make money, any traction...")
    url = st.text_input("Website (optional)", placeholder="https://example.com")
    deck = st.file_uploader("Pitch deck PDF (optional)", type=["pdf"])
    go = st.button("Analyse venture", type="primary", use_container_width=True)
    st.divider()
    st.subheader("Recent runs")
    try:
        for r in api("GET", "/runs", params={"limit": 8}, timeout=10).json():
            label = f"{r.get('title') or 'Untitled'} · {r['recommendation'] or r['status']}"
            if st.button(label, key=f"hist-{r['run_id']}", use_container_width=True, disabled=r["status"] != "done"):
                st.query_params["run"] = r["run_id"]
                st.session_state.pop("result", None)
                st.rerun()
    except Exception:
        st.caption("History unavailable.")
    st.divider()
    st.caption(f"Backend: `{BACKEND_URL}`")
    if GRAFANA_URL:
        st.caption(f"[Ops dashboards]({GRAFANA_URL})")
    st.caption("[Source on GitHub](https://github.com/AshmitCajla/VenturesLab)")


def run_analysis():
    files = {"file": (deck.name, deck.getvalue(), "application/pdf")} if deck else None
    try:
        r = api("POST", "/analyze", data={"idea": idea, "url": url}, files=files, timeout=60)
    except requests.RequestException as exc:
        st.error(f"Cannot reach the backend ({exc}). If it is on a free host it may be waking up; "
                 "try again in 30 seconds.")
        return
    if r.status_code != 202:
        st.error(f"Backend rejected the request: {r.status_code} {r.text[:300]}")
        return
    run_id = r.json()["run_id"]
    agents = agent_list()
    with st.status("Running the analysis...", expanded=True) as status:
        bar, log = st.progress(0.0), st.empty()
        while True:
            time.sleep(2)
            try:
                job = api("GET", f"/runs/{run_id}").json()
            except requests.RequestException:
                continue
            done = [p for p in job["progress"] if p.get("number")]
            bar.progress(min(1.0, len(done) / max(1, len(agents) or 13)))
            log.markdown("\n".join(
                f"{ {'ok': '✅', 'error': '⚠️', 'skipped': '⏭️'}.get(p.get('status'), '•') } **{md(p['name'])}**"
                + (f" · {p['ms'] / 1000:.1f}s" if p.get("ms") else "")
                + (" · self-corrected" if p.get("attempts", 1) > 1 else "")
                + (f" · {md(p.get('error'))}" if p.get("error") else "")
                for p in job["progress"]))
            if job["status"] in ("done", "failed"):
                break
        if job["status"] == "failed":
            status.update(label="Analysis failed", state="error")
            st.error(job["error"])
            return
        status.update(label=f"Analysis complete in {job['elapsed_s']:.0f}s", state="complete", expanded=False)
    for w in job.get("warnings", []):
        st.warning(w)
    st.query_params["run"] = run_id
    st.session_state["result"], st.session_state["run_id"] = job["result"], run_id


if go:
    if not (idea.strip() or url.strip() or deck):
        st.error("Add a description, a website or a pitch deck.")
    else:
        run_analysis()

if "result" not in st.session_state and st.query_params.get("run"):
    try:
        job = api("GET", f"/runs/{st.query_params['run']}").json()
        if job.get("status") == "done":
            st.session_state["result"], st.session_state["run_id"] = job["result"], job["run_id"]
        else:
            st.info(f"Run {st.query_params['run']} is {job.get('status', 'unknown')}.")
    except Exception as exc:
        st.error(f"Could not load run: {exc}")

data, run_id = st.session_state.get("result"), st.session_state.get("run_id")
if not data:
    st.markdown("## Evaluate a startup in a few minutes")
    st.markdown(
        "Describe a venture, add its website or pitch deck. Specialised agents research the market and "
        "competitors, check the deck's own numbers against independent sources, size the market and score "
        "the venture with a fixed rubric. Every section shows its sources and how much evidence it rests on. "
        "Hidden text and instructions aimed at the AI are stripped from decks before anything reads them.")
    st.stop()

# ------------------------------------------------------------------ results
score, rec = data.get("score", 0.0) or 0.0, data.get("recommendation", "n/a")
profile = data.get("venture_profile", {})
evals = data.get("evaluation_summary", {})
sec = data.get("security") or {}
cost = data.get("cost") or {}

st.header(profile.get("name", "Venture analysis") if ok(profile) else "Venture analysis")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Venture score", f"{score:.0%}")
c2.metric("Recommendation", rec)
c3.metric("Quality checks", f"{evals.get('passed', 0)}/{evals.get('total', 0)}")
c4.metric("Model cost", f"${cost.get('spent_usd', 0):.4f}")
c5.download_button("Download PDF", build_pdf(data), file_name="ventureslab_report.pdf",
                   mime="application/pdf", use_container_width=True)
st.progress(min(max(score, 0.0), 1.0))
if ok(data.get("scoring")):
    st.info(md(data["scoring"]["verdict_rationale"]))
    confidence_badge(data, "scoring")
if sec.get("hidden_text") or sec.get("injections"):
    st.error(f"🛡️ This deck tried to manipulate the analysis: {sec.get('hidden_text', 0)} hidden passage(s) and "
             f"{sec.get('injections', 0)} instruction(s) aimed at the AI were removed before analysis. "
             "See the Integrity tab.")
if data.get("degraded_agents"):
    st.warning("Some sections are unavailable (" + ", ".join(data["degraded_agents"]) + "); the rest of the report "
               "is complete.")
hist = data.get("history") or {}
if hist.get("previous_version"):
    ch = hist["previous_version"]["changes"]
    st.info(("🔁 Same deck as the last run; differences are model variance: " if ch.get("same_deck")
             else "🔁 Updated deck since the last version: ") + describe_changes(ch))
if hist.get("similar"):
    st.caption("🧭 Similar to past decks: " + ", ".join(
        f"{s['company']} ({s['recommendation']}, similarity {s['similarity']:.2f})" for s in hist["similar"]))

tabs = st.tabs(["Overview", "Deck claims", "Market & competition", "Strategy", "Validation", "Execution",
                "Integrity", "Ask", "Quality & trace"])

with tabs[0]:
    a, b = st.columns(2)
    with a, st.container(border=True):
        st.subheader("Venture profile")
        if ok(profile):
            for k in ("problem", "solution", "customer", "business_model", "stage", "painkiller_or_vitamin"):
                st.markdown(f"**{k.replace('_', ' ').title()}:** {md(profile.get(k, ''))}")
            confidence_badge(data, "venture_profile")
            sources(data, "venture_profile")
        else:
            st.error(profile.get("error", "unavailable"))
    with b, st.container(border=True):
        st.subheader("Score breakdown")
        sc = data.get("scoring", {})
        if ok(sc):
            for k, w in sc.get("weights", {}).items():
                st.markdown(f"**{k.replace('_', ' ').title()}** ({w:.0%} weight): {sc[k]['score']}/10")
                st.caption(md(sc[k]["reason"]))
            st.caption("The final score is a fixed weighted sum computed in code, not a number the model chose.")

with tabs[1]:
    claims = data.get("claims") or {}
    if claims.get("claims"):
        s = claims["summary"]
        st.markdown(f"**{s.get('conflict', 0)} conflict(s)** with independent sources · "
                    f"{s.get('consistent', 0)} consistent · {s.get('unverifiable', 0)} unverifiable · "
                    f"{s.get('citations_not_found', 0)} citation(s) not found on the slide")
        for c in claims["claims"]:
            with st.container(border=True):
                st.markdown(f"**{md(c['claim'])}** · slide {c['slide']} · {STATUS.get(c['status'], c['status'])}"
                            + (" · ⚠️ quote not found on slide" if c["citation"] != "verified" else ""))
                st.caption(f"Deck says: “{md(c['quote'])}”")
                if c.get("external_estimate"):
                    st.markdown(f"Independent estimate: **{md(c['external_estimate'])}**")
                st.caption(md(c.get("note", "")))
                for u in c.get("sources", []):
                    st.caption(u)
    else:
        st.info(md(claims.get("skipped") or claims.get("error") or "Claims are checked when a pitch deck is provided."))

with tabs[2]:
    ms, mk = data.get("market_size", {}), data.get("market_analysis", {})
    if ok(ms):
        m1, m2, m3 = st.columns(3)
        m1.metric("TAM", usd(ms["tam_usd"]))
        m2.metric("SAM", usd(ms["sam_usd"]))
        m3.metric("SOM", usd(ms["som_usd"]))
        confidence_badge(data, "market_size")
        with st.expander("Bottom-up assumptions"):
            for x in ms.get("assumptions", []):
                st.markdown(f"- {md(x)}")
        sources(data, "market_size")
    if ok(mk):
        st.markdown(f"**Industry:** {md(mk['industry'])} · **Market:** {md(mk['market_size_estimate'])} "
                    f"· **CAGR:** {md(mk['cagr'])} · **Attractiveness:** {mk['market_attractiveness_score']}/10")
        confidence_badge(data, "market_analysis")
        sources(data, "market_analysis")
    comps = data.get("competitors", [])
    st.subheader("Competitors")
    if isinstance(comps, list) and comps and "error" not in comps[0]:
        st.dataframe(comps, use_container_width=True, hide_index=True)
        confidence_badge(data, "competitors")
        sources(data, "competitors")
    else:
        st.warning("Competitor analysis unavailable.")

with tabs[3]:
    a, b = st.columns(2)
    swot, pestle = data.get("swot", {}), data.get("pestle", {})
    with a, st.container(border=True):
        st.subheader("SWOT")
        if ok(swot):
            s1, s2 = st.columns(2)
            s1.success("**Strengths**\n" + "".join(f"\n- {md(i)}" for i in swot["strengths"]))
            s2.error("**Weaknesses**\n" + "".join(f"\n- {md(i)}" for i in swot["weaknesses"]))
            s1.info("**Opportunities**\n" + "".join(f"\n- {md(i)}" for i in swot["opportunities"]))
            s2.warning("**Threats**\n" + "".join(f"\n- {md(i)}" for i in swot["threats"]))
        else:
            st.caption(md(swot.get("skipped") or swot.get("error") or "unavailable"))
    with b, st.container(border=True):
        st.subheader("PESTLE")
        if ok(pestle):
            for k, v in pestle.items():
                with st.expander(k.title()):
                    st.write(md(v))
        else:
            st.caption(md(pestle.get("skipped") or pestle.get("error") or "unavailable"))

with tabs[4]:
    a, b = st.columns(2)
    porter, cust = data.get("porter", {}), data.get("customer_validation", {})
    with a, st.container(border=True):
        st.subheader("Porter's Five Forces")
        if ok(porter):
            for k, f in porter.items():
                st.markdown(f"**{k.replace('_', ' ').title()}** ({f['score']}/10)")
                st.progress(f["score"] / 10)
                st.caption(md(f["reason"]))
        else:
            st.caption(md(porter.get("skipped") or porter.get("error") or "unavailable"))
    with b, st.container(border=True):
        st.subheader("Customer pain")
        if ok(cust):
            st.metric("Pain score", f"{cust['customer_pain_score']}/10")
            st.write(f"**{cust['real_pain_or_nice_to_have'].title()}**, frequency: {md(cust['frequency_of_problem'])}")
            flags = {"Faster": cust["solution_is_faster"], "Cheaper": cust["solution_is_cheaper"],
                     "Easier": cust["solution_is_easier"], "Tech advantage": cust["technological_advantage"]}
            st.write(" · ".join(f"{'✅' if v else '❌'} {k}" for k, v in flags.items()))
            confidence_badge(data, "customer_validation")
            sources(data, "customer_validation")

with tabs[5]:
    if rec != "BUILD":
        st.info("Execution agents run only for ventures scored BUILD. Address the weaknesses above and re-run.")
    else:
        rd, gtm, fin = data.get("roadmap", {}), data.get("gtm_strategy", {}), data.get("financial_model", {})
        a, b, c = st.columns(3)
        with a, st.container(border=True):
            st.subheader("MVP roadmap")
            if ok(rd):
                st.markdown(f"**0-30 days:** {md(rd['days_30']['validation_plan'])}")
                st.markdown(f"**30-90 days:** {md(rd['days_90']['mvp_development'])}")
                st.markdown(f"**12 months:** {md(rd['months_12']['pmf_goal'])}")
                st.caption("KPIs: " + md(", ".join(rd["months_12"]["kpis"])))
        with b, st.container(border=True):
            st.subheader("Go-to-market")
            if ok(gtm):
                st.markdown(f"**ICP:** {md(gtm['ideal_customer_profile'])}")
                st.markdown(f"**Positioning:** {md(gtm['positioning'])}")
                st.markdown(f"**Pricing:** {md(gtm['pricing_strategy'])}")
                st.markdown("**Channels:** " + md(", ".join(gtm["marketing_channels"])))
        with c, st.container(border=True):
            st.subheader("Financials")
            if ok(fin):
                st.metric("Funding required", usd(fin["funding_required_usd"]))
                st.metric("Year-1 revenue", usd(fin["year_1_revenue_usd"]))
                st.metric("Year-5 revenue", usd(fin["year_5_revenue_usd"]))
                confidence_badge(data, "financial_model")

with tabs[6]:
    st.subheader("Input integrity")
    st.markdown(f"Defences {'on' if sec.get('defenses_enabled', True) else '**off**'} · semantic screen: "
                f"{sec.get('semantic_screen', 'n/a')} · {sec.get('hidden_text', 0)} hidden passage(s), "
                f"{sec.get('injections', 0)} instruction(s) removed")
    if sec.get("findings"):
        st.dataframe(sec["findings"], use_container_width=True, hide_index=True)
    else:
        st.success("Nothing hidden or manipulative was found in the inputs.")
    plan = (data.get("plan") or {}).get("agents") or {}
    if plan:
        st.subheader("Plan")
        st.caption(f"Chosen by: {data['plan'].get('source')}")
        st.dataframe([{"agent": k, "ran": v["run"], "reason": v["reason"]} for k, v in plan.items()],
                     use_container_width=True, hide_index=True)

with tabs[7]:
    st.subheader("Ask about this report")
    st.caption("Answers come only from this run's data and the deck's slides.")
    chat = st.session_state.setdefault(f"chat-{run_id}", [])
    for m in chat:
        st.chat_message(m["role"]).markdown(md(m["text"]))
    if run_id and (q := st.chat_input("Why is the market score low?")):
        chat.append({"role": "user", "text": q})
        try:
            ans = api("POST", f"/runs/{run_id}/ask", json={"question": q, "history": [
                {"role": m["role"], "text": m["text"]} for m in chat[:-1]]}, timeout=120).json()
            chat.append({"role": "assistant", "text": ans.get("answer", str(ans))})
        except Exception as exc:
            chat.append({"role": "assistant", "text": f"Could not answer: {exc}"})
        st.rerun()
    if run_id:
        st.divider()
        f1, f2, _ = st.columns([1, 1, 6])
        if f1.button("👍 Useful"):
            api("POST", f"/runs/{run_id}/feedback", json={"rating": "up"})
            st.toast("Thanks for the feedback")
        if f2.button("👎 Not useful"):
            api("POST", f"/runs/{run_id}/feedback", json={"rating": "down"})
            st.toast("Thanks for the feedback")

with tabs[8]:
    st.subheader("Agent trace")
    st.dataframe([{k: (", ".join(t[k]) if isinstance(t.get(k), list) else t.get(k))
                   for k in ("number", "agent", "status", "ms", "attempts", "models", "cost_usd", "checks_passed",
                             "checks_total", "error")} for t in data.get("trace", [])],
                 use_container_width=True, hide_index=True)
    if cost.get("by_model"):
        st.subheader("Cost by model")
        st.dataframe([{"model": k, **v} for k, v in cost["by_model"].items()], use_container_width=True, hide_index=True)
    st.subheader("Failed quality checks")
    failed = evals.get("failed", [])
    if failed:
        st.dataframe(failed, use_container_width=True, hide_index=True)
    else:
        st.success("Every automated quality check passed.")
    if data.get("trace_id"):
        st.caption(f"OpenTelemetry trace id: `{data['trace_id']}` · run id: `{run_id}`")
