# Slack and MCP setup

## Slack bot

1. https://api.slack.com/apps → **Create New App → From an app manifest** → paste
   `backend/slack_app/manifest.yml`.
2. **Install to Workspace**; copy the *Bot User OAuth Token* (`xoxb-…`) to `SLACK_BOT_TOKEN`.
3. **Basic Information → App-Level Tokens → Generate** with scope `connections:write`; copy it
   (`xapp-…`) to `SLACK_APP_TOKEN`. Socket Mode means no public URL is needed.
4. Optional: `SLACK_OPS_CHANNEL` (channel id for incidents and ops questions),
   `SLACK_DIGEST_CHANNEL` (weekly digest), `PUBLIC_UI_URL` (where "Full report" links point).
5. Restart the API. The log says `Slack bot connected (Socket Mode)`. Invite the bot to a channel.

| Do this | You get |
|---|---|
| Drop a PDF deck in a channel | A thread with one progress message edited live, then a summary card (score, verdict, red flags, claim conflicts, buttons) |
| `@VenturesLab https://acme.com fintech for dentists` | The same, from a website and description |
| Reply in the run's thread | An answer from that run's data and slides |
| 👍 / 👎 / ✏️ Correct a section | Feedback stored; corrections become evaluation cases |
| 🔄 Re-run | The deck is fetched again from Slack and re-analysed (the diff shows what changed) |
| Open the app's Home tab | Your recent runs and workspace stats |
| `@VenturesLab` in the ops channel ("what broke last night?") | The ops agent answers from telemetry |
| An alert fires (Alertmanager → `/ops/alerts`) | An incident thread in the ops channel with a diagnosis and runbook; a postmortem draft when it resolves |
| `/ventureslab-digest` | This week's digest (also posted Mondays 09:00) |

## MCP

Three servers, all in `backend/mcp_servers/`:

| Server | Tools | Used by |
|---|---|---|
| `ventureslab_server` | `analyze_deck`, `get_run`, `list_runs`, `ask_run`, `compare_decks` | Claude Desktop, other agents |
| `ops_server` | `slo_status`, `recent_runs`, `error_signatures`, `agent_health`, `recent_logs`, `query_prometheus`, `get_trace`, `list_runbooks`, `read_runbook`, `incident_history` | the ops agent; you, from Claude Desktop |
| `research_server` | `web_search`, `deck_search`, `similar_ventures` | the analysis agents, through the allow-listed gateway |

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ventureslab": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "mcp_servers.ventureslab_server"],
      "cwd": "/path/to/VenturesLab/backend"
    },
    "ventureslab-ops": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "mcp_servers.ops_server"],
      "cwd": "/path/to/VenturesLab/backend"
    }
  }
}
```

Then ask Claude: "Analyse ~/Downloads/deck.pdf with VenturesLab and compare it with the last deck from
the same company", or "Using ventureslab-ops, what failed in the last 6 hours?". The servers share the
API's database (`DB_PATH`), so runs show up everywhere.
