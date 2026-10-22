# Failure modes

What breaks, what the system does about it, and how we know. "Tested by" names the test or
harness that exercises it.

| Failure | Behaviour | Signal | Tested by |
|---|---|---|---|
| Primary LLM provider down | Calls fall back to the next provider; the trace records which model answered | `ventureslab_llm_calls_total{outcome="error"}`, `LLMErrorRateHigh` | `evaluation/chaos.py` (primary_provider_down), `test_chaos_provider_down_degrades_instead_of_failing` |
| All providers down / timeouts | Agent fails, dependents skipped, report delivered with sections unavailable | `ReportCompletenessBudgetBurn`; error signature `llm_timeout` | chaos `llm_timeouts`, `test_agent_failure_is_isolated` |
| Rate limits (Groq TPM/TPD) | Calls are paced under each model's tokens/minute budget; on 429 waits as long as Groq asks (capped at 65s) and falls back | `retrying in` log lines; signature `rate_limit`; `LLMErrorRateHigh` | `test_token_pacer_waits_instead_of_exceeding_the_limit`, live chaos |
| A fallback model is dead (404, retired) | Dropped for the rest of the call instead of retried; next fallback used | canary `ventureslab_llm_target_up`, `FallbackModelDown` | `test_permanently_broken_target_is_dropped_not_retried`, `test_canary_reports_each_target` |
| Errors absorbed by retries/fallbacks | Report is complete, but each agent's trace lists the recovered errors | error signatures; `LLMErrorRateHigh` | `test_errors_absorbed_by_retries_stay_visible_to_ops` |
| Ops agent's model is down during an incident | Rule-based diagnosis from error signatures, low confidence | diagnosis summary says "model unavailable" | `test_diagnosis_falls_back_to_rules_when_the_model_is_down` |
| Model returns prose / bad JSON | Native JSON-schema output; retry; fallback | signature `model_output_format` | `test_transient_model_error_is_retried` |
| Output well-formed but wrong (SOM > SAM, self as competitor…) | 20+ checks; one self-correction retry with the failure explained | `ventureslab_quality_checks_total`, Quality tab | `test_self_correction_retries_bad_market_sizing` |
| Web search down | Agents use model knowledge, say so; confidence drops to Low | `WebSearchDegraded`; trace warnings | chaos `web_search_down` |
| Deck unreadable | Run continues on description/website with a warning | `DeckParsingFailures` | chaos `malformed_decks` |
| Burst of requests | Queued behind `MAX_CONCURRENT_JOBS`; nothing dropped | `ventureslab_runs_queued`, `RunQueueBacklog` | chaos `request_burst` |
| Server restart mid-run | Interrupted runs are marked failed on start, never left "running" | `RunSuccessBudgetBurn` if frequent | `test_interrupted_runs_are_failed_on_restart` |
| Cost runaway | Optional agents skipped before the per-run budget is exceeded | `ventureslab_budget_skips_total` | `test_optional_agents_skip_when_budget_is_spent` |
| Hidden instructions in the deck | Removed before any model sees them; reported | `ventureslab_injection_findings_total` | `tests/test_security.py`, `evaluation/redteam.py` |
| Hidden fake evidence | Removed (hidden text); visible false claims are checked by verification | claim `conflict` / `citation_not_found` | red team `fake_*`, `test_market_claim_conflict_is_flagged` |
| Instructions inside images | Vision transcribes, then the same quarantine and screen run | finding on the slide | red team `image_instruction` (needs a vision model) |
| Paraphrased instructions | Semantic screen (live) and spotlighting | finding `semantic screen: …` | red team `visible_paraphrased` (live) |
| Model invents a citation | Quote checked against the slide text | `citations_not_found` in claims summary | `test_hallucinated_citation_is_marked` |
| Semantic screen unavailable | Fails open, reported as `semantic_screen: unavailable` in the result | Integrity tab | `test_semantic_screen_fails_open_and_says_so` |
| SSRF via website or Slack file URL | Only public http(s) for websites; only Slack-hosted file URLs for downloads | warning on the run | `test_blocks_private_urls`, `test_slack_downloads_only_from_slack` |
| Slack API errors | Logged; never fail a run | `slack post failed` logs | handlers swallow and log |

## Known gaps

* **Visible lies.** A deck that simply states fake traction in plain sight is not an injection;
  internal metrics (revenue, customers) cannot be checked against public sources and are labelled
  "unverifiable" rather than guessed. Human diligence is still required.
* **Confidence is a heuristic**, not a calibrated probability. Consistency runs measure stability;
  calibrating confidence against outcomes needs labelled history we do not have yet.
* **Single instance.** SQLite and in-process background tasks assume one API process.
* **Free-tier limits** make large live evaluations slow (Groq's 120b model: 200k tokens/day).
