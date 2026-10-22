# Runbook: cost budget pressure

**Category:** `cost_budget`

Optional agents are skipped with "cost budget reached" (`ventureslab_budget_skips_total`).

## Check
1. `histogram_quantile(0.95, rate(ventureslab_run_cost_usd_bucket[1h]))` against `RUN_BUDGET_USD`.
2. Which model is spending: `sum by (model) (increase(ventureslab_llm_cost_usd_total[1h]))`.
3. Self-corrections double an agent's cost: look at `attempts` in traces.

## Mitigate
- Prices changed: update `core/pricing.py` or `PRICES_JSON`.
- A deck is unusually long: verification and the screen scale with slides; cap with `VISION_MAX_SLIDES`.
- Raise `RUN_BUDGET_USD` deliberately, not as a reflex.
