# Slack and MCP setup

## Slack bot (free)

Everything here works on Slack's free plan: Socket Mode needs no public URL, server or paid add-on.

1. **Workspace.** Use an existing one where you can install apps, or create a free one at
   <https://slack.com/get-started#/createnew>.
2. **App.** <https://api.slack.com/apps> → *Create New App* → *From an app manifest* → pick the
   workspace → paste `backend/slack_app/manifest.yml` → *Create*.
3. **Bot token.** *Install App* → *Install to Workspace* → *Allow*. Copy the *Bot User OAuth Token*
   (`xoxb-…`) into `backend/.env` as `SLACK_BOT_TOKEN`.
4. **App token.** *Basic Information* → *App-Level Tokens* → *Generate Token and Scopes*, add the scope
   `connections:write`, generate, and copy it (`xapp-…`) into `backend/.env` as `SLACK_APP_TOKEN`.
5. **Channels.** In Slack create `#deals` (where decks are dropped) and `#ops-alerts`, and in each run
   `/invite @VenturesLab`. Copy the ops channel's ID (channel name → *About* → bottom of the panel,
   `C0…`) into `SLACK_OPS_CHANNEL`, and the same or another channel's ID into `SLACK_DIGEST_CHANNEL`.
6. **Check.** `make slack-check` verifies the tokens, scopes, Socket Mode and channels, prints the exact
   fix for anything wrong, and posts a test message to the ops channel.
7. **Run.** `make api` (or `docker compose up`). The log shows `Slack bot connected (Socket Mode)`.
   Set `PUBLIC_UI_URL` if the UI is not on `http://localhost:8501`, so "Full report" links work.

Tokens live only in `backend/.env`, which git ignores. Rotate them under *OAuth & Permissions* and
*Basic Information* if they are ever exposed.

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

Then, in Claude Desktop: "Analyse ~/Downloads/deck.pdf with VenturesLab and compare it with the last deck from
the same company", or "Using ventureslab-ops, what failed in the last 6 hours?". The servers share the
API's database (`DB_PATH`), so runs show up everywhere.
