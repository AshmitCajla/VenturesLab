# 2-minute demo script

Setup: `docker compose up --build`, Slack bot connected, ops channel open in a second window,
Grafana Ops dashboard on a third screen. Decks: `python -m evaluation.golden --fake` writes nothing,
so generate them once:

```bash
cd backend && python -c "from evaluation.golden import CONSTRUCTED, _pdf; c=CONSTRUCTED[0]; open('/tmp/dentaflow.pdf','wb').write(_pdf(c['slides']))"
cd backend && python -c "from evaluation.redteam import ATTACKS, build_deck; open('/tmp/fake_traction.pdf','wb').write(build_deck(next(a for a in ATTACKS if a.id=='fake_white')))"
```

| Time | Show | Say |
|---|---|---|
| 0:00-0:10 | Slack channel | "VenturesLab is an analyst that lives in Slack. I drop a pitch deck in." |
| 0:10-0:35 | Drop `dentaflow.pdf`; progress message updates live; card arrives | "Thirteen agents, planned per deck, running in parallel. The card: PIVOT, and a red flag: the deck claims a $50B market, four independent reports say $3-4B." |
| 0:35-0:50 | Click Full report → Deck claims tab with sources | "Every number links to its slide and its sources. Citations are checked in code against the slide text." |
| 0:50-1:00 | Reply in thread: "why is the market score low?" | "Follow-ups are answered from this run's evidence only." |
| 1:00-1:20 | Drop `fake_traction.pdf` | "This deck hides fake traction in white text. Looks normal to a human. VenturesLab removed it before any model saw it; here's the integrity tab." Show the live red-team table (`backend/evaluation/redteam_report_live.md`) for the fabricated-evidence rows. |
| 1:20-1:40 | `curl -X POST localhost:8000/chaos -d '{"name":"provider_down","params":{"provider":"primary"}}'` and run two decks | "Now I take down the primary model provider. Reports still arrive via fallback; the completeness SLO starts burning." Grafana: burn-rate panel. |
| 1:40-2:00 | Ops channel: incident thread with diagnosis + runbook | "The alert opened an incident. The ops agent pulled metrics, logs and traces over MCP, named the provider outage, and linked the runbook. When it resolves it drafts the postmortem." |

Close on the README numbers table.
