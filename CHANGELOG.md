# Changelog

Notable changes. Dates are release dates.

## 3.0.0 (unreleased)

### Added
- Claim verification agent: quotes checked against slide text, market claims cross-checked with search.
- Per-section sources and evidence-based confidence; vision for charts and images.
- Prompt-injection defences in four layers; red-team harness with attack-success-rate reporting.
- Planner, dependency-wired parallel graph, run cost budget, search cache, cross-deck memory.
- Slack bot, MCP servers, persistent run store, follow-up Q&A, feedback and corrections.
- OpenTelemetry, Prometheus metrics, SLOs with burn-rate alerts, Grafana dashboards, fallback canary.
- Ops agent (incident threads, diagnosis, postmortems) and chaos harness.
- Golden set, consistency, judge calibration, model comparison and a PR quality gate.

- API keys and per-key rate limits; MCP served at `/mcp` inside the API; MCP proxy mode for local
  clients against a deployment; `/ventureslab` slash command.
- Production deployment: Caddy (automatic HTTPS), production compose override, backups, Render blueprint.

### Changed
- One LLM call path with a provider chain, per-model token pacing and cost accounting.
- Evaluation inputs live in `evaluation/data/`, generated reports in `evaluation/reports/`.

## 2.0.0
- Twelve-agent analysis with RAG, structured outputs, quality checks and self-correction; async job API.
