# Common tasks. Run `make help` for the list.
PY      ?= python3
BACKEND := backend
VENV    ?= .venv
BIN     = $(abspath $(VENV))/bin

.DEFAULT_GOAL := help
.PHONY: help install lint format test cov gate eval redteam chaos slack-check api ui up down prod prod-logs backup dashboards clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Create a virtualenv with backend, frontend and dev dependencies
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -r $(BACKEND)/requirements-dev.txt -r frontend/requirements.txt
	$(BIN)/pre-commit install

lint:  ## Lint and check formatting
	cd $(BACKEND) && $(BIN)/ruff check . ../frontend ../deploy && $(BIN)/ruff format --check . ../frontend ../deploy

format:  ## Apply formatting and safe lint fixes
	cd $(BACKEND) && $(BIN)/ruff check --fix . ../frontend ../deploy && $(BIN)/ruff format . ../frontend ../deploy

test:  ## Run the offline test suite
	cd $(BACKEND) && $(BIN)/pytest -q

cov:  ## Run tests with a coverage report
	cd $(BACKEND) && $(BIN)/pytest -q --cov --cov-report=term-missing

gate:  ## Quality gate: red team, detector false positives, chaos signals, checks
	cd $(BACKEND) && $(BIN)/python -m evaluation.gate

eval:  ## Offline evaluation harness (fake models)
	cd $(BACKEND) && $(BIN)/python -m evaluation.run_eval --fake

redteam:  ## Red team against real models (needs API keys)
	cd $(BACKEND) && $(BIN)/python -m evaluation.redteam --live

chaos:  ## Chaos experiments against real models (needs API keys)
	cd $(BACKEND) && $(BIN)/python -m evaluation.chaos --live --runs 2

slack-check:  ## Verify Slack tokens, scopes, Socket Mode and channels
	cd $(BACKEND) && $(BIN)/python -m slack_app.check --post

api:  ## Run the API with reload on :8000
	cd $(BACKEND) && $(BIN)/uvicorn main:app --reload --port 8000

ui:  ## Run the Streamlit UI on :8501
	cd frontend && $(BIN)/streamlit run app.py

up:  ## Full stack: API, UI, Prometheus, Alertmanager, Grafana, Jaeger
	docker compose up --build

down:  ## Stop the stack
	docker compose down

PROD := docker compose -f docker-compose.yml -f docker-compose.prod.yml

prod:  ## Production on a VM: HTTPS via Caddy, only 80/443 exposed (see docs/deployment.md)
	$(PROD) up -d --build

prod-logs:  ## Follow production API logs
	$(PROD) logs -f --tail=100 backend

backup:  ## Back up the production SQLite database
	./scripts/backup.sh

dashboards:  ## Regenerate Grafana dashboards from code
	$(PY) deploy/grafana/build_dashboards.py

clean:  ## Remove caches and local run data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(BACKEND)/.coverage $(BACKEND)/data
