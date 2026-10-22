# Chaos experiment report (live models)

Date: 2026-10-22 · gpt-oss-20b/120b on Groq (free tier), fallbacks nemotron-3.5-lightning:free and nemotron-3-ultra:free on OpenRouter · 1 run per experiment.

| experiment | fault | delivered | sections unavailable | alerts fired | top error signals | ops agent diagnosis | time (s) |
|---|---|---|---|---|---|---|---|
| baseline | — | 1/1 | 0.0 | LLMErrorRateHigh, WebSearchDegraded | rate_limit, llm_timeout, model_output_format | — | 431.5 |
| llm_timeouts | llm_timeout (60% of calls) | 1/1 | 1.0 | LLMErrorRateHigh, ReportCompletenessBudgetBurn, RunLatencyBudgetBurn | llm_timeout, model_output_format | did not run: Groq daily token limit reached (see below) | 1387.0 |
| primary_provider_down | provider_down (Groq) | 1/1 | 1.0 | ReportCompletenessBudgetBurn | llm_provider_outage | did not run: Groq daily token limit reached (see below) | 96.3 |

An earlier live run (same day, before several fixes below) also covered `web_search_down` and
`malformed_decks`: both delivered complete reports, `WebSearchDegraded` fired for the search outage,
and the top signals were `web_search` and `deck_parsing`. Its ops-agent diagnoses were correct for
provider outage, search outage and deck parsing, **but are not counted**: that run's alert text named
the experiment, so the answer was leaked (see below).

## What the live runs found, and what changed because of them

* **Every run delivered a report.** With Groq down, the report came from the OpenRouter fallbacks;
  with 60% of LLM calls timing out, one section was marked unavailable and the rest was delivered.
* **The right signal was on top**: `llm_timeout` for injected timeouts, `llm_provider_outage` for the
  provider outage, and `rate_limit` as real background noise on the free tier in the baseline (which
  correctly fired `LLMErrorRateHigh`).
* **The default free fallback model was dead** (404 from its upstream provider). Fallbacks are now a
  list per role, permanently failing targets are dropped instead of retried, and a canary probes every
  model in the chain every 15 minutes (`FallbackModelDown`).
* **Parallel agents tripped Groq's 8k tokens/minute limit.** A per-model token pacer now queues calls
  instead of burning retries on 429s.
* **Faults absorbed by retries were invisible** (an earlier run diagnosed `unknown` for timeouts).
  Recovered errors are now recorded per agent and counted in error signatures.
* **The ops agent depended on the provider that was failing.** Its diagnoses hit Groq's daily token
  limit (200k on gpt-oss-120b) in this run. It now falls back to a rule-based diagnosis from error
  signatures, labelled low confidence.
* **The harness leaked the answer.** The earlier run's alert text named the experiment ("chaos
  experiment malformed_decks") and the agent's diagnoses quoted it. Alerts now carry only what
  Alertmanager would send.
* **One failed search in one run fired `WebSearchDegraded`** (baseline row). Symptom alerts now need
  at least two affected runs.

Still to measure live with the neutral alert: ops-agent diagnosis accuracy for every fault, and
`request_burst`. Run `python -m evaluation.chaos --live --runs 2` after the daily budget resets, or
from the "Live evaluation" workflow.
