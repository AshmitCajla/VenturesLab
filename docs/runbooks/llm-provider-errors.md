# Runbook: LLM provider errors (rate limits, outages, malformed output)

**Alerts:** `ReportCompletenessBudgetBurn`, `RunSuccessBudgetBurn`, `LLMErrorRateHigh`
**Category from the ops agent:** `rate_limit`, `llm_provider_outage`, `model_output_format`

## Check
1. Which provider and model? `sum by (provider, model, outcome) (increase(ventureslab_llm_calls_total[15m]))`
   or ask the bot: `@VenturesLab ops which provider is failing?`
2. `error_signatures` (ops MCP tool): `429` / `tokens per day` means quota, `Connection` / `5xx` means outage,
   `Failed to generate JSON` / `tool_use_failed` means the model is not following the schema.
3. Are fallbacks answering? Run traces list `models`; `openrouter:*` entries mean the fallback took over.

## Mitigate
- **Daily quota (Groq TPD)**: it resets on a rolling 24h window. Lower `MAX_CONCURRENT_JOBS`, set
  `LLM_PROVIDER=openrouter` to make the fallback primary, or move to a paid tier.
- **Per-minute limits (TPM/RPM)**: the client already waits as long as Groq asks; reduce concurrency.
- **Outage**: confirm on the provider status page; the fallback chain covers it if `OPENROUTER_API_KEY` is set.
- **Schema failures**: check whether a model was changed (`FAST_MODEL`, `REASONING_MODEL`); roll back.

## Verify
`ventureslab_llm_calls_total{outcome="error"}` rate back to baseline; `GET /slo` burn rates below 1.
