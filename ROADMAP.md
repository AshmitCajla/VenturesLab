# VenturesLab v3 roadmap

Goal: VenturesLab stops being "an app that analyzes decks" and becomes an AI analyst teammate that
lives in Slack, shows its sources, defends itself against manipulated decks, monitors itself and
explains its own failures.

Status legend: ✅ built and tested · 🟡 built, needs a human or a live measurement · ⬜ not started

## 1. Every claim is checked against evidence ✅
- ✅ Sections link to the slides and URLs that were in front of the agent (`evidence`).
- ✅ Verification agent: headline claims with slide + quote; quotes checked against slide text in code;
  market claims cross-checked with independent sources (live: "$50B TAM" vs "$2.8-4.4B", 4 sources).
- ✅ Per-section confidence from evidence coverage (`workflow/confidence.py`).
- ✅ Vision: charts and images transcribed (free OpenRouter VL model), then screened like any text.

## 2. Resistance to manipulated decks ✅ / 🟡
- ✅ Four layers: rendering-based hidden-text removal, regex quarantine, semantic screen, spotlighting.
- ✅ Red-team corpus: instruction attacks (9 hiding techniques incl. image) and fabricated-evidence attacks.
- ✅ Offline ASR in CI and gated: instruction 9/11 → 2/11, evidence 5/5 → 1/5 (simulated gullible model).
- 🟡 Live ASR v1: 0/10 → 0/10 (instruction payloads did not move a weak venture). Live v2 numbers for the
  fabricated-evidence family: see `backend/evaluation/reports/redteam_report_live.md`; rerun with more repeats
  when the Groq daily budget allows.

## 3. A smarter agent system ✅
- ✅ Planner chooses optional agents; rules keep core agents and regulated-industry PESTLE.
- ✅ Graph wired by real data dependencies; independent agents run in parallel.
- ✅ Cost routing (fast vs reasoning), per-run budget, persistent search cache.
- ✅ Memory: similar past ventures; diff against the previous version of the same company's deck.

## 4. MCP throughout ✅
- ✅ VenturesLab as an MCP server (`analyze_deck`, `get_run`, `ask_run`, `compare_decks`, `list_runs`).
- ✅ Research tools as an MCP server behind per-agent allow-lists; ops tools as an MCP server.

## 5. Slack as the main interface ✅ / 🟡
- ✅ Deck → thread, live progress message, summary card with buttons; thread follow-ups; corrections
  modal; App Home; weekly digest; `/ventureslab-digest`. Handlers tested with a fake Slack client.
- 🟡 Needs a Slack workspace and tokens to try for real (`docs/slack-and-mcp.md`).

## 6. SRE-grade observability ✅ / 🟡
- ✅ OpenTelemetry spans (run → agent → LLM/tool), Prometheus metrics, JSON logs, one run id.
- ✅ Three SLOs (success, completeness, latency) with multi-window burn-rate alerts; symptom alerts.
- ✅ Grafana dashboards as code: Ops, Cost, Customer experience.
- 🟡 The Prometheus/Grafana/Jaeger stack is validated in CI (`promtool`, `amtool`); bring it up with
  `docker compose up` to see the dashboards fill.

## 7. Ops agent ✅
- ✅ Alertmanager webhook → incident thread → diagnosis over MCP tools with runbook → postmortem.
- ✅ "What broke last night?" from Slack, the API or Claude Desktop (verified live).

## 8. Chaos testing ✅
- ✅ Faults: LLM timeouts, provider down, search down, corrupt decks, request bursts.
- ✅ Offline: every fault degrades gracefully and the top error signal names it (6/6, gated in CI).
- ✅ Live: see `backend/evaluation/reports/chaos_report_live.md`.

## 9. Evaluation you can trust 🟡
- ✅ Golden-set runner with constructed decks (labels true by construction).
- 🟡 Hand-labelled public decks: add 20-30 to `backend/evaluation/data/golden/` (instructions in its README).
- ✅ Consistency runner (score sd, verdict flip rate, per-criterion sd).
- ✅ LLM judge that refuses to be trusted below 20 human labels / kappa 0.6; corrections loop.
- ✅ PR quality gate; manual live-eval workflow; cost-vs-quality comparison across model configs.

## 10. Showcase 🟡
- ✅ One-command setup (`docker compose up --build`), docs (architecture, decisions, failure modes,
  engineering notes, Slack/MCP setup), demo script.
- 🟡 Record the 2-minute video (`docs/demo-script.md`); deploy a demo workspace; write-up with live numbers.

## Constraints
- Groq's free tier (200k tokens/day on gpt-oss-120b) limits live evaluation; the live-eval workflow and
  `--resume` flags exist for running it in pieces.
- Only numbers from real runs are published; offline numbers test plumbing and deterministic layers.
