# Contributing

## Setup

```bash
make install          # .venv with backend, frontend and dev dependencies, plus pre-commit hooks
cp backend/.env.example backend/.env   # only needed for live runs
```

## Before opening a PR

```bash
make lint             # ruff check + ruff format --check
make test             # offline tests, no API keys
make gate             # red team, detector false positives, chaos signals, quality checks
```

CI runs the same three. `make format` applies formatting.

## Conventions

* **Commits**: imperative subject, at most ~72 characters, prefixed with the area when it helps
  (`security:`, `agents:`, `ops:`, `slack:`, `eval:`, `docs:`, `ci:`). Body explains *why*.
* **Branches**: `feature/…`, `fix/…`, `chore/…`. Squash or rebase before merge; no merge commits.
* **Tests**: every behaviour change comes with a test. Offline tests use `tests/fakes.py`; never call a
  real provider from a test (the test config blanks all keys on purpose).
* **Prompts and models**: a change to a prompt, model default or provider chain needs a live check
  (`make redteam`, `make chaos` or `python -m evaluation.golden`) and the result in the PR.
* **Baselines**: if a change intentionally moves a gated metric, run
  `python -m evaluation.gate --update-baseline` and explain the move in the PR.
* **Dashboards** are generated: edit `deploy/grafana/build_dashboards.py`, then `make dashboards`.
* **Secrets** only through environment variables. `backend/.env` is ignored by git.

## Layout

See the "Project layout" section of the README and `docs/architecture.md`.
