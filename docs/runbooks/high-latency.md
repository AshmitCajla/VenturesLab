# Runbook: runs are slow

**Alerts:** `RunLatencyBudgetBurn`
**Category:** `llm_timeout`, `capacity`

## Check
1. Is it every run or one agent? `agent_health` (ops tool) shows p95 per agent.
2. Queue: `ventureslab_runs_queued` above 0 for long means runs wait for a slot (`MAX_CONCURRENT_JOBS`).
3. Provider latency: `histogram_quantile(0.95, sum by (le, provider) (rate(ventureslab_llm_latency_seconds_bucket[10m])))`.
4. Rate-limit waits look like latency: look for `retrying in` lines with `recent_logs`.

## Mitigate
- Provider slow: switch primary (`LLM_PROVIDER`), or lower `LLM_TIMEOUT_S` so fallbacks start sooner.
- Queue: raise `MAX_CONCURRENT_JOBS` only if provider limits allow it; otherwise this is a capacity limit.
- Planner: confirm it still drops optional agents (`plan` in results); `PLANNER=1`.

## Verify
p95 under the SLO target in `GET /slo` (`windows.1h`).
