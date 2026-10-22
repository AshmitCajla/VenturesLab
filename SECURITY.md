# Security

## Reporting

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/AshmitCajla/VenturesLab/security/advisories/new), not in a
public issue. Expect an acknowledgement within a week.

## Scope

In scope: prompt injection that changes a score or verdict, bypasses of the hidden-text or instruction
defences, SSRF through website URLs or Slack file downloads, authentication of the Alertmanager
webhook, leakage of API keys or Slack tokens, and anything that lets one Slack user read another's runs.

## Design notes

* Decks, websites, search results and Slack messages are untrusted input; see `services/security.py`
  and `docs/design-decisions.md`.
* Secrets come only from environment variables; tests blank them so a developer's `.env` cannot leak in.
* Fault injection (`/chaos`) is disabled unless `CHAOS=1`; never enable it in production.
