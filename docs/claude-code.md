# How Claude Code was used, and how its output was checked

Most of v3 was written with Claude Code (Anthropic's coding agent) from a written vision of ten
layers. The rule throughout: **nothing counts as done until something other than the model that
wrote it says so** (a test, a harness, a live run, or a tool such as `promtool`).

## Workflow

1. **Plan in the repo.** The vision was turned into `ROADMAP.md`, ordered by dependency (defences
   before Slack, evaluation before the planner, observability before the ops agent).
2. **Probe before building.** Before choosing a design, Claude Code tested the real options against
   live APIs: PyMuPDF's span data for hidden-text detection; Llama Prompt Guard 2 and
   gpt-oss-safeguard on the paraphrased payload; OpenRouter's model list and the account's tier
   (which showed paid fallback models would fail on this key); free vision models on an image
   containing an instruction; the MCP SDK 2.x API surface.
3. **Build in small slices, each with tests.** Offline tests use a deterministic fake model, so the
   whole graph, Slack handlers, MCP servers, incidents and chaos run in CI without keys.
4. **Measure with harnesses, not impressions.** Red team, chaos, golden set, consistency and the
   quality gate each produce a report; the gate blocks regressions on every PR.
5. **Run it for real.** Live runs against Groq/OpenRouter, checked in the browser.

## What the checks caught (all fixed)

| Caught by | Problem in generated code or design |
|---|---|
| Live red-team run | The harness counted rate-limited (incomplete) runs as "attack blocked", which would have overstated the defences. Now marked incomplete and resumable. |
| Live red-team run | Instruction attacks did not move a weak venture's score even with defences off (0/10), so that corpus could not show a before/after. Led to the fabricated-evidence corpus on a middling venture. |
| Offline chaos harness | Web-search outages and unreadable decks degraded silently: no error, no signal, nothing for the ops agent to find. Now recorded as warnings, grouped into error signatures, and alerted on. |
| Offline chaos harness | A warm search cache hid a search outage during the experiment. |
| Benign-copy corpus (gate) | The injection regexes flagged "New task: onboarding takes 15 minutes" and "Developer: Jane Doe". |
| MCP integration test | MCP 2.x returns a list result as one content block per item; joining them produced invalid JSON for the ops agent. |
| OpenRouter API probe | Defaulting fallbacks to the same model family (gpt-oss) would 402 on a free-tier key. |
| Live end-to-end run | The verification agent searched with the whole customer description and got no results, so the $50B TAM went "unverifiable". It now asks the extractor for a short query; the next live run flagged it as a conflict against four market reports ($2.8-4.4B). |
| Live end-to-end run | "No results" from search was treated as an outage, which would page on-call for nothing. |
| Live end-to-end run | The deck-version diff keyed claims by the model's wording, so re-running the same deck showed every claim as removed and re-added. Now keyed by kind and slide, and identical decks are labelled so model variance is not mistaken for change. |
| Test isolation review | `config.py` loads `backend/.env`; tests would have picked up a developer's real API keys and Slack tokens. Tests now blank them. |

## What was deliberately not trusted

* Model-reported confidence (computed from evidence instead).
* Model-chosen final scores (fixed formula).
* Model citations (checked against slide text).
* The LLM judge, until it agrees with human labels (`evaluation/judge.py` refuses below 20 labels or
  kappa 0.6).
* Offline red-team numbers as evidence of the semantic screen or fencing (the simulated model obeys
  anything it sees; only `--live` numbers measure those layers).
