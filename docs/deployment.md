# Deployment

Two free paths. Both run the API, the Slack bot (Socket Mode) and the MCP endpoint (`/mcp`) in one
backend process, so there is a single thing to deploy and monitor.

| | A. Render + Streamlit Cloud | B. One VM (Oracle Cloud Always Free) |
|---|---|---|
| Cost | $0 | $0 (card needed for identity check) |
| Setup time | ~20 min | ~45 min |
| What runs | API + Slack bot + MCP; UI on Streamlit Cloud | Everything, including Prometheus, Alertmanager, Grafana, Jaeger, Caddy (HTTPS) |
| Always on | With a 10-minute external ping (below) | Yes |
| Data survives restarts | No (free disk is ephemeral) | Yes (Docker volumes + daily backups) |
| SLOs and alerts | `GET /slo` and the ops agent (computed from the database) | Prometheus burn-rate alerts → Alertmanager → ops agent → Slack |
| Good for | Demos, the portfolio link | The full SRE story: dashboards, alerts, incident threads |

Free-tier facts change; these were checked in October 2026: Render free web services sleep after
15 minutes without inbound traffic and get 750 instance hours a month
([docs](https://render.com/docs/free)); Oracle's Always Free Ampere allowance is now 2 OCPUs and
12 GB RAM ([InfoQ](https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/)); free Docker
Spaces on Hugging Face may now need a paid plan, so they are not used here.

Generate API keys once and keep them in a password manager:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## A. Render (API, Slack, MCP) + Streamlit Community Cloud (UI)

1. **Backend.** Render → *New* → *Blueprint* → select this repository. `render.yaml` creates one free
   Docker web service. Fill in the variables it asks for:
   * `GROQ_API_KEY`, `OPENROUTER_API_KEY`
   * `API_KEYS`: two generated keys, comma-separated (one for the UI, one for you / MCP clients)
   * Slack tokens and channel ids (see the Slack section of [slack-and-mcp.md](slack-and-mcp.md))
   * `PUBLIC_API_URL`: `https://<service>.onrender.com`; `PUBLIC_UI_URL`: the Streamlit URL from step 2
2. **UI.** <https://share.streamlit.io> → *New app* → this repo, branch `main`, file `frontend/app.py`.
   *Advanced settings* → *Secrets*:
   ```toml
   BACKEND_URL = "https://<service>.onrender.com"
   API_KEY = "<the UI's key from API_KEYS>"
   ```
3. **Keep it awake.** Slack's Socket Mode is an outbound connection, which Render does not count as
   traffic, so without inbound requests the service sleeps and the bot goes offline. Create a free
   monitor at <https://cron-job.org> or <https://uptimerobot.com> that calls
   `https://<service>.onrender.com/health` every 10 minutes. One always-on service uses ~744 of the 750
   monthly hours.
4. **Check.**
   ```bash
   curl https://<service>.onrender.com/health
   curl -H "X-API-Key: <key>" https://<service>.onrender.com/slo
   ```
   Then drop a deck in Slack.

Limits on this path: 512 MB RAM (keep `MAX_CONCURRENT_JOBS=1`), runs are lost when the service
redeploys or restarts, and there is no Prometheus/Grafana (the ops agent still works from the
database and logs). Set `SUPABASE_URL`/`SUPABASE_KEY` to keep a copy of finished analyses.

---

## B. One VM with the full stack (Oracle Cloud Always Free)

1. **VM.** Oracle Cloud → *Compute* → *Create instance* → shape *VM.Standard.A1.Flex* with 2 OCPUs /
   12 GB, Ubuntu 24.04. In the subnet's security list, allow inbound TCP 80 and 443. (On the VM,
   Ubuntu images also need `sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT`
   followed by `sudo netfilter-persistent save`.)
2. **Docker.**
   ```bash
   curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Code and secrets.**
   ```bash
   sudo mkdir -p /opt/ventureslab && sudo chown $USER /opt/ventureslab
   git clone https://github.com/AshmitCajla/VenturesLab.git /opt/ventureslab && cd /opt/ventureslab
   cp backend/.env.example backend/.env && nano backend/.env    # model keys, API_KEYS, Slack
   ```
   And a `.env` next to `docker-compose.yml` for the stack itself:
   ```bash
   DOMAIN=203-0-113-7.sslip.io        # your VM's public IP with dashes; or your own domain
   ACME_EMAIL=you@example.com          # for the Let's Encrypt certificate
   GRAFANA_ADMIN_PASSWORD=<generated>
   ALERT_WEBHOOK_TOKEN=<generated>     # shared by Alertmanager and the API
   UI_API_KEY=<one of API_KEYS>
   ```
   [sslip.io](https://sslip.io) resolves `203-0-113-7.sslip.io` and its subdomains to that IP, so no
   domain purchase is needed. With your own domain, point `@`, `api` and `grafana` A records at the VM.
4. **Start.**
   ```bash
   make prod          # docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
   make prod-logs     # watch for "Slack bot connected (Socket Mode)"
   ```
   * UI: `https://$DOMAIN`
   * API and MCP: `https://api.$DOMAIN` (`/mcp`), with an API key
   * Grafana: `https://grafana.$DOMAIN` (user `admin`); Prometheus, Alertmanager and Jaeger are only
     reachable inside the Docker network (use `ssh -L 9090:localhost:9090` if you need them, after
     adding a port mapping, or browse them through Grafana).
5. **Backups.** `crontab -e` and add
   `0 3 * * * cd /opt/ventureslab && ./scripts/backup.sh >> backups/backup.log 2>&1`.
   Restore: stop the stack, gunzip a backup into the `ventureslab-data` volume as `ventureslab.db`.
6. **Updates.** `git pull && make prod`. Runs in flight during a restart are marked failed on start
   (they show as "interrupted by a server restart"), never left hanging.

---

## Connecting MCP clients to a deployment

**Claude Desktop** (stdio proxy: nothing to host locally except the code):

```json
{
  "mcpServers": {
    "ventureslab": {
      "command": "/path/to/VenturesLab/.venv/bin/python",
      "args": ["-m", "mcp_servers.ventureslab_server"],
      "cwd": "/path/to/VenturesLab/backend",
      "env": {
        "VENTURESLAB_API_URL": "https://api.example.com",
        "VENTURESLAB_API_KEY": "<one of API_KEYS>"
      }
    }
  }
}
```

**Clients with remote MCP and custom headers** (Claude Code, most agent frameworks) connect straight to
the endpoint:

```bash
claude mcp add --transport http ventureslab https://api.example.com/mcp --header "Authorization: Bearer <key>"
```

The ops tools server (`mcp_servers.ops_server`) reads the database and log files directly, so run it on
the VM (`docker compose exec backend python -m mcp_servers.ops_server`) or through SSH as the command.

---

## Production checklist

- [ ] `API_KEYS` set; the UI and each MCP client use their own key (revoke one by removing it)
- [ ] `ALERT_WEBHOOK_TOKEN` set (Alertmanager → `/ops/alerts`)
- [ ] `CHAOS` unset (fault injection is refused unless `CHAOS=1`)
- [ ] `make slack-check` passes on the server
- [ ] `/health/models?probe=true` shows every model in the chain up
- [ ] Backups scheduled and one restore tried
- [ ] Uptime monitor on `/health`
