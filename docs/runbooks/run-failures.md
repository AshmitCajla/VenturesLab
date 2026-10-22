# Runbook: runs failing (general)

**Alerts:** `RunSuccessBudgetBurn`
**Category:** `code_bug`, `unknown`

## Check
1. `error_signatures` for the last hour: the top signature is usually the answer.
2. `recent_runs status=failed`: is it every source (API, Slack, MCP) or one?
3. Open one failed run's trace (`get_trace run_id`) and look at the first span with an error.
4. Was there a deploy? Compare the first failure time with the deploy time.

## Mitigate
- After a deploy: roll back first, investigate second.
- "interrupted by a server restart": the process crashed or was redeployed mid-run; check host memory.

## Verify
`GET /slo`: `windows.5m.success_ratio` back at target, burn rate falling.
