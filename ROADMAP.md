# VenturesLab v3 roadmap

Goal: VenturesLab stops being "an app that analyzes decks" and becomes an AI analyst teammate that
lives in Slack, shows its sources, defends itself against manipulated decks, monitors itself and
explains its own failures.

The ten layers of the vision are ordered below by dependency, not by the order they were written.
Each phase ends in something that can be demoed and measured on its own.

## Where v2 stands

| Already in place | Gap for v3 |
|---|---|
| 12 agents on a LangGraph graph, 3 parallel phases | Fixed graph; no planner |
| RAG over deck chunks, each chunk knows its slide number | Report does not cite slides; text only, no vision |
| Score is a fixed weighted sum computed in code | Criterion scores still come from an LLM that reads untrusted text |
| 20+ quality checks, one self-correction retry | No golden set, no judge calibration, no consistency runs |
| `fast` / `reasoning` model roles, Groq → OpenRouter fallback | No cost tracking, budgets or caching |
| Per-agent trace (ms, attempts, checks) in the job result | No OpenTelemetry, metrics, SLOs or alerts |
| Async job API, failure isolation | Jobs live in memory: lost on restart, so no history, App Home or digest |
| CI: lint, tests, offline eval, Docker builds | Eval does not gate merges on quality |

## Phase 1: Resistance to manipulated decks (layer 2) ← in progress

Comes first because every later layer (Slack uploads, external evidence, memory) adds more untrusted
input, and because it links VenturesLab to the prompt-injection paper.

- [x] Treat decks, websites and web-search results as untrusted data. Wrap each in a fenced block with
      a random boundary, and prepend a fixed security preamble to every agent prompt (spotlighting).
- [x] Hidden-text detection at extraction time: white or near-background text, sub-4pt text, text
      outside the page, invisible render mode, near-transparent text. Hidden text is dropped before
      chunking and reported as a finding.
- [x] Instruction detector on visible text (deck and website): passages that address the model
      ("ignore previous instructions", "rate this 10/10", fake system/role tags) are quarantined, not
      passed to agents, and reported.
- [x] Score integrity: weights stay in code; a `security` section is attached to every result; a
      check flags uniformly perfect criterion scores.
- [x] Red-team corpus generated in code (`evaluation/redteam.py`): visible injection, white text,
      tiny text, off-page text, invisible text, website injection, plus a benign control deck.
- [x] Attack-success-rate harness with defenses on and off (`INJECTION_DEFENSES=0|1`). Offline mode
      uses a deliberately gullible simulated model, which tests the plumbing in CI. `--live` measures
      against the real models.
- [x] Live ASR run (gpt-oss on Groq): 0/10 without defences, 0/10 with. Instruction payloads did not
      move a weak venture's score; the scoring agent only reads other agents' outputs, never the deck.
      Result is in the README. Runs with a failed agent are marked incomplete and can be resumed.
- [ ] Harder corpus that can show a real before/after: hidden *fabricated evidence* (fake ARR, fake
      customer logos, fake LOIs) instead of instructions, a mid-quality control venture where small
      shifts flip the verdict, and N repeats per case so score noise is measured, not guessed.
- [ ] Instructions inside images and misleading charts: these depend on vision (phase 2). Add them
      to the corpus once decks are read with a vision model, since image text is invisible to v2.

## Phase 2: Every claim is checked against evidence (layer 1)

- Citations: agents return `claims[]` with `{text, source: "slide:7" | url, quote}`. A check verifies
  that the quoted span really appears in the cited slide or page (catches invented citations).
- Vision ingestion: render each slide to an image and send slides with charts or tables to a vision
  model for a structured extraction of the numbers. Run it through the same phase-1 defenses.
- Verification agent: for each headline number in the deck (TAM, growth, revenue, users), search
  independent sources and return `consistent | conflict | unverifiable` with the range found.
  "Deck claims a $50B TAM; three sources put it at $6–9B."
- Per-section confidence computed from evidence coverage (cited claims / total claims, source
  agreement), not self-reported by the model.

## Phase 3: Evaluation you can trust (layer 9)

Must land before the planner and model routing, otherwise there is no way to tell whether they
made the analysis better or worse.

- Golden set: 20–30 real public decks with hand-checked labels (verdict range, key numbers, known
  red flags). Store labels as JSON next to the PDFs.
- LLM judge for free-text quality, checked against the hand labels (agreement rate) before it is used.
- Consistency: run each deck N times and report the score's standard deviation and verdict flip rate.
- PR gate: a CI job runs the eval on a small fixed subset and fails when check pass rate, verdict
  accuracy or ASR gets worse than the baseline committed in the repo.
- Cost vs. quality table across models.

## Phase 4: SRE-grade observability (layer 6)

- Persistent job store (Postgres; SQLite locally) replacing the in-memory `JOBS` dict. Needed for
  history, App Home, digests and SLOs.
- OpenTelemetry spans for run → agent → LLM call / tool call, with token counts and cost as span
  attributes; one `run_id` shared by traces, logs, metrics and the Slack thread.
- Prometheus metrics: run outcomes, agent latency histograms, LLM errors by provider, queue depth,
  cost per run.
- SLOs: 99% of runs succeed, p95 latency under a target set from measured baselines. Multi-window
  burn-rate alerts (e.g. 14.4× over 1h and 6× over 6h).
- Grafana dashboards as code: Ops, Cost, Customer experience. Local stack in `docker-compose`.

## Phase 5: A smarter agent system (layer 3)

- Planner: a cheap model reads the venture profile and chooses which agents to run; the graph is
  built per run. The dependency rules stay in code, so the planner cannot skip scoring inputs.
- Cost routing: extraction on the fast model, reasoning on the strong one, a per-run budget that
  degrades gracefully (skip optional agents) instead of failing, and a cache for repeated searches.
- Memory: embed each finished venture profile. "This looks like 3 decks you analyzed before."
  For an updated deck from the same company, show what changed since the last version.

## Phase 6: Slack as the main interface (layer 5)

- Bolt app (Socket Mode for dev). A deck dropped in a channel → thread reply that edits one live
  progress message, then a summary card with buttons: full report, re-run, 👍/👎.
- Follow-ups in the thread are answered from that run's stored context and evidence.
- Corrections from analysts are saved as labelled cases for the eval set.
- App Home with the user's past runs; weekly digest on a schedule.

## Phase 7: MCP throughout (layer 4)

- VenturesLab as an MCP server: `analyze_deck`, `get_report`, `compare_decks`. Usable from Claude
  Desktop. This is cheap and can be pulled forward at any time.
- Tool servers for search, market data, retrieval, memory and observability, with a per-agent
  allow-list. Keep it to boundaries that are real (external data, ops tooling); wrapping in-process
  functions as servers adds latency without adding isolation.

## Phase 8: Ops agent and chaos testing (layers 7 and 8)

- Alertmanager webhook → incident thread in Slack. The ops agent pulls traces, logs and metrics
  through the observability MCP tools, posts its best guess at the cause and links the runbook.
- Postmortem draft after resolution. Direct questions: "what broke last night?"
- Fault injection behind a flag: model timeouts, a tool down, malformed decks, a burst of requests.
  For each fault: does the run degrade to a partial report, does the alert fire, does the ops agent
  name the right cause? Publish the table.

## Phase 9: Showcase (layer 10)

- One-command setup (`docker compose up` with a seeded demo), live demo workspace.
- Docs: architecture, design decisions, failure modes, how Claude Code was used and how its output
  was checked.
- 2-minute video: deck into Slack → report with evidence → adversarial deck caught → injected
  failure → ops agent diagnoses it.
- Technical write-up with the measured numbers from phases 1, 3, 4 and 8.

## Constraints to plan around

- Groq's free tier (~8k tokens/min per model) limits consistency runs, live ASR runs and chaos tests.
  Budget for a paid key or run those overnight with backoff.
- Only publish numbers that come from real runs. Offline (fake-model) results test plumbing only.
