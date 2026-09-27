"""VenturesLab - Streamlit front end.

Talks to the FastAPI backend (BACKEND_URL). Submits a job, then polls it and
shows each of the 12 agents finishing live.
"""
from __future__ import annotations

import os
import time

import requests
import streamlit as st

from report import build_pdf, ok, usd

try:
    BACKEND_URL = st.secrets.get("BACKEND_URL", os.getenv("BACKEND_URL", "http://localhost:8000"))
except Exception:  # no secrets.toml
    BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
BACKEND_URL = BACKEND_URL.rstrip("/")

st.set_page_config(page_title="VenturesLab", page_icon="📈", layout="wide")


def md(text) -> str:
    """Escape '$' so Streamlit does not render money as LaTeX."""
    return str(text).replace("$", r"\$")


@st.cache_data(ttl=3600)
def agent_list():
    try:
        return requests.get(f"{BACKEND_URL}/agents", timeout=15).json()
    except Exception:
        return []


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📈 VenturesLab")
    st.caption("12 AI analysts review a startup idea the way a VC fund would.")
    idea = st.text_area("Startup description", height=140,
                        placeholder="What problem, for whom, how you make money, any traction...")
    url = st.text_input("Website (optional)", placeholder="https://example.com")
    deck = st.file_uploader("Pitch deck PDF (optional)", type=["pdf"])
    go = st.button("Analyse venture", type="primary", use_container_width=True)
    st.divider()
    st.caption(f"Backend: `{BACKEND_URL}`")
    st.caption("[Source on GitHub](https://github.com/AshmitCajla/VenturesLab)")


def run_analysis():
    files = {"file": (deck.name, deck.getvalue(), "application/pdf")} if deck else None
    try:
        r = requests.post(f"{BACKEND_URL}/analyze", data={"idea": idea, "url": url}, files=files, timeout=60)
    except requests.RequestException as exc:
        st.error(f"Cannot reach the backend ({exc}). If it is on a free host it may be waking up; "
                 "try again in 30 seconds.")
        return
    if r.status_code != 202:
        st.error(f"Backend rejected the request: {r.status_code} {r.text[:300]}")
        return
    job_id = r.json()["job_id"]

    agents = agent_list()
    with st.status("Running the 12-agent analysis...", expanded=True) as status:
        bar = st.progress(0.0)
        log = st.empty()
        while True:
            time.sleep(2)
            try:
                job = requests.get(f"{BACKEND_URL}/jobs/{job_id}", timeout=30).json()
            except requests.RequestException:
                continue
            done = [p for p in job["progress"] if p.get("number")]
            bar.progress(min(1.0, len(done) / max(1, len(agents) or 12)))
            log.markdown("\n".join(
                f"{'✅' if p['status'] == 'ok' else '⚠️'} **{p['name']}**"
                + (f" · {p['ms'] / 1000:.1f}s" if p.get("ms") else "")
                + (" · self-corrected" if p.get("attempts", 1) > 1 else "")
                + (f" · {p.get('error')}" if p.get("error") else "")
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
    st.session_state["result"] = job["result"]


if go:
    if not (idea.strip() or url.strip() or deck):
        st.error("Add a description, a website or a pitch deck.")
    else:
        run_analysis()

data = st.session_state.get("result")
if not data:
    st.markdown("## Evaluate a startup idea in a few minutes")
    st.markdown(
        "Describe a venture (and optionally add its website or pitch deck). Twelve specialised agents "
        "research the market and competitors, run SWOT, PESTLE and Porter's Five Forces, size the market "
        "bottom-up and score the venture. Ideas that score high enough get a roadmap, go-to-market plan "
        "and financial model. Every agent's output is checked automatically, and failing outputs are "
        "sent back to the agent to fix.")
    st.stop()

# ------------------------------------------------------------------ results
score, rec = data.get("score", 0.0), data.get("recommendation", "n/a")
profile = data.get("venture_profile", {})
evals = data.get("evaluation_summary", {})

st.header(profile.get("name", "Venture analysis") if ok(profile) else "Venture analysis")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Venture score", f"{score:.0%}")
c2.metric("Recommendation", rec)
c3.metric("Quality checks passed", f"{evals.get('passed', 0)}/{evals.get('total', 0)}")
c4.download_button("Download PDF report", build_pdf(data), file_name="ventureslab_report.pdf",
                   mime="application/pdf", use_container_width=True)
st.progress(min(max(score, 0.0), 1.0))
if ok(data.get("scoring")):
    st.info(md(data["scoring"]["verdict_rationale"]))

tabs = st.tabs(["Overview", "Market & competition", "Strategy", "Validation", "Execution", "Quality & trace"])

with tabs[0]:
    a, b = st.columns(2)
    with a, st.container(border=True):
        st.subheader("Venture profile")
        if ok(profile):
            for k in ("problem", "solution", "customer", "business_model", "stage", "painkiller_or_vitamin"):
                st.markdown(f"**{k.replace('_', ' ').title()}:** {md(profile.get(k, ''))}")
        else:
            st.error(profile.get("error", "unavailable"))
    with b, st.container(border=True):
        st.subheader("Score breakdown")
        sc = data.get("scoring", {})
        if ok(sc):
            for k, w in sc.get("weights", {}).items():
                st.markdown(f"**{k.replace('_', ' ').title()}** ({w:.0%} weight): {sc[k]['score']}/10")
                st.caption(md(sc[k]["reason"]))

with tabs[1]:
    ms, mk = data.get("market_size", {}), data.get("market_analysis", {})
    if ok(ms):
        m1, m2, m3 = st.columns(3)
        m1.metric("TAM", usd(ms["tam_usd"]))
        m2.metric("SAM", usd(ms["sam_usd"]))
        m3.metric("SOM", usd(ms["som_usd"]))
        with st.expander("Bottom-up assumptions"):
            for x in ms.get("assumptions", []):
                st.markdown(f"- {md(x)}")
    if ok(mk):
        st.markdown(f"**Industry:** {md(mk['industry'])} · **Market:** {md(mk['market_size_estimate'])} "
                    f"· **CAGR:** {md(mk['cagr'])} · **Attractiveness:** {mk['market_attractiveness_score']}/10")
        for s in mk.get("sources", []):
            st.caption(s)
    comps = data.get("competitors", [])
    st.subheader("Competitors")
    if isinstance(comps, list) and comps and "error" not in comps[0]:
        st.dataframe(comps, use_container_width=True, hide_index=True)
    else:
        st.warning("Competitor analysis unavailable.")

with tabs[2]:
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
    with b, st.container(border=True):
        st.subheader("PESTLE")
        if ok(pestle):
            for k, v in pestle.items():
                with st.expander(k.title()):
                    st.write(md(v))

with tabs[3]:
    a, b = st.columns(2)
    porter, cust = data.get("porter", {}), data.get("customer_validation", {})
    with a, st.container(border=True):
        st.subheader("Porter's Five Forces")
        if ok(porter):
            for k, f in porter.items():
                st.markdown(f"**{k.replace('_', ' ').title()}** ({f['score']}/10)")
                st.progress(f["score"] / 10)
                st.caption(md(f["reason"]))
    with b, st.container(border=True):
        st.subheader("Customer pain")
        if ok(cust):
            st.metric("Pain score", f"{cust['customer_pain_score']}/10")
            st.write(f"**{cust['real_pain_or_nice_to_have'].title()}**, frequency: {md(cust['frequency_of_problem'])}")
            flags = {"Faster": cust["solution_is_faster"], "Cheaper": cust["solution_is_cheaper"],
                     "Easier": cust["solution_is_easier"], "Tech advantage": cust["technological_advantage"]}
            st.write(" · ".join(f"{'✅' if v else '❌'} {k}" for k, v in flags.items()))

with tabs[4]:
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

with tabs[5]:
    st.subheader("Agent trace")
    st.dataframe([{k: t.get(k) for k in ("number", "agent", "status", "ms", "attempts", "checks_passed",
                                           "checks_total", "error")} for t in data.get("trace", [])],
                 use_container_width=True, hide_index=True)
    st.subheader("Failed quality checks")
    failed = evals.get("failed", [])
    if failed:
        st.dataframe(failed, use_container_width=True, hide_index=True)
    else:
        st.success("Every automated quality check passed.")
