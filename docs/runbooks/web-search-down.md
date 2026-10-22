# Runbook: web search is degraded

**Alerts:** `WebSearchDegraded` (ticket)
**Category:** `web_search`

Runs still finish, but market and competition sections are based on model knowledge only and their
confidence drops to Low ("no web sources").

## Check
1. `error_signatures`: `web search unavailable: …` with the exception (DNS, timeout, rate limit).
2. `ventureslab_tool_calls_total{tool="web_search", outcome="error"}`.
3. From the API host: `python -c "from ddgs import DDGS; print(DDGS().text('test', max_results=1))"`.

## Mitigate
- DDGS rate-limits bursts: lower concurrency. Cached results (7 days) keep repeated queries working.
- Network/DNS on the host: fix egress; nothing in the app needs changing.
- Temporarily set `WEB_SEARCH=0` so reports say "web search disabled" instead of failing each call.

## Verify
New runs show web sources in `evidence.market_analysis.web`.
