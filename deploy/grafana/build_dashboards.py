"""Generates the three Grafana dashboards (Ops, Cost, Customer experience) as JSON.

    python deploy/grafana/build_dashboards.py

Dashboards are code: edit the panel lists below, regenerate, commit the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}
X = 'source!~"eval|redteam"'


def ts(title, exprs, unit="short", w=12, h=8, legend=True, thresholds=None):
    return {
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "gridPos": {"w": w, "h": h},
        "fieldConfig": {
            "defaults": {"unit": unit, **({"thresholds": thresholds} if thresholds else {})},
            "overrides": [],
        },
        "options": {"legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom"}},
        "targets": [
            {"expr": e, "legendFormat": lf, "refId": chr(65 + i), "datasource": DS} for i, (e, lf) in enumerate(exprs)
        ],
    }


def stat(title, expr, unit="short", w=6, h=4, thresholds=None, decimals=None):
    return {
        "type": "stat",
        "title": title,
        "datasource": DS,
        "gridPos": {"w": w, "h": h},
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                **({"decimals": decimals} if decimals is not None else {}),
                "thresholds": thresholds or {"mode": "absolute", "steps": [{"color": "green", "value": None}]},
            },
            "overrides": [],
        },
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background"},
        "targets": [{"expr": expr, "refId": "A", "datasource": DS}],
    }


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in pairs]}


def dashboard(uid, title, description, panels):
    x = y = 0
    row_h = 0
    for i, p in enumerate(panels):
        w, h = p["gridPos"]["w"], p["gridPos"]["h"]
        if x + w > 24:
            x, y = 0, y + row_h
            row_h = 0
        p["gridPos"].update(x=x, y=y)
        p["id"] = i + 1
        x += w
        row_h = max(row_h, h)
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "schemaVersion": 39,
        "version": 1,
        "time": {"from": "now-24h", "to": "now"},
        "refresh": "30s",
        "tags": ["ventureslab"],
        "panels": panels,
        "templating": {"list": []},
        "annotations": {"list": []},
    }


OPS = dashboard(
    "vl-ops",
    "VenturesLab · Ops",
    "Errors, latency, queue depth and SLO burn.",
    [
        stat(
            "Success SLI (1h)",
            "1 - sli:run_errors:ratio_rate1h",
            "percentunit",
            decimals=2,
            thresholds=steps((None, "red"), (0.95, "orange"), (0.99, "green")),
        ),
        stat(
            "Error budget left (30d)",
            "slo:run_success:error_budget_remaining",
            "percentunit",
            decimals=0,
            thresholds=steps((None, "red"), (0.25, "orange"), (0.5, "green")),
        ),
        stat(
            "Burn rate (1h)",
            "sli:run_errors:ratio_rate1h / 0.01",
            "x",
            decimals=1,
            thresholds=steps((None, "green"), (1, "orange"), (14.4, "red")),
        ),
        stat("Runs active", "sum(ventureslab_runs_active) + 0", "short"),
        ts(
            "Runs by outcome",
            [
                (f"sum by (status) (increase(ventureslab_runs_total{{{X}}}[10m]))", "{{status}}"),
                (f"sum(increase(ventureslab_runs_degraded_total{{{X}}}[10m]))", "degraded"),
            ],
        ),
        ts(
            "SLI vs objective",
            [
                ("1 - sli:run_errors:ratio_rate1h", "success (1h)"),
                ("1 - sli:run_incomplete:ratio_rate1h", "complete reports (1h)"),
                ("vector(0.99)", "success objective"),
                ("vector(0.95)", "completeness objective"),
            ],
            "percentunit",
        ),
        ts(
            "Run duration p50 / p95",
            [
                ("histogram_quantile(0.5, sum by (le) (rate(ventureslab_run_duration_seconds_bucket[15m])))", "p50"),
                ("histogram_quantile(0.95, sum by (le) (rate(ventureslab_run_duration_seconds_bucket[15m])))", "p95"),
                ("vector(450)", "SLO threshold"),
            ],
            "s",
        ),
        ts(
            "Agent latency p95",
            [
                (
                    "histogram_quantile(0.95, sum by (le, agent) (rate(ventureslab_agent_duration_seconds_bucket[15m])))",
                    "{{agent}}",
                )
            ],
            "s",
        ),
        ts(
            "LLM errors by provider / model",
            [
                (
                    'sum by (provider, model) (rate(ventureslab_llm_calls_total{outcome="error"}[5m]))',
                    "{{provider}} {{model}}",
                )
            ],
            "reqps",
        ),
        ts(
            "LLM latency p95 by provider",
            [
                (
                    "histogram_quantile(0.95, sum by (le, provider) (rate(ventureslab_llm_latency_seconds_bucket[10m])))",
                    "{{provider}}",
                )
            ],
            "s",
        ),
        ts("Agent outcomes", [("sum by (status) (rate(ventureslab_agent_runs_total[5m]))", "{{status}}")], "ops"),
        ts("Queue depth", [("ventureslab_runs_queued", "queued"), ("ventureslab_runs_active", "active")]),
        ts(
            "Tool calls",
            [("sum by (tool, outcome) (rate(ventureslab_tool_calls_total[5m]))", "{{tool}} {{outcome}}")],
            "ops",
        ),
        ts(
            "Deck parse failures / injections removed",
            [
                ("increase(ventureslab_deck_parse_failures_total[15m])", "decks unreadable"),
                ("sum by (kind) (increase(ventureslab_injection_findings_total[15m]))", "{{kind}} removed"),
            ],
        ),
    ],
)

COST = dashboard(
    "vl-cost",
    "VenturesLab · Cost",
    "Spend per run and per model, tokens, cache, budget.",
    [
        stat("Spend (24h)", "sum(increase(ventureslab_llm_cost_usd_total[24h]))", "currencyUSD", decimals=3),
        stat(
            "Avg cost per run (24h)",
            "sum(increase(ventureslab_run_cost_usd_sum[24h])) / clamp_min(sum(increase(ventureslab_run_cost_usd_count[24h])), 1)",
            "currencyUSD",
            decimals=4,
        ),
        stat(
            "Cache hit ratio (24h)",
            'sum(increase(ventureslab_cache_total{result="hit"}[24h])) / clamp_min(sum(increase(ventureslab_cache_total[24h])), 1)',
            "percentunit",
            decimals=0,
        ),
        stat("Agents skipped for budget (24h)", "sum(increase(ventureslab_budget_skips_total[24h])) + 0", "short"),
        ts(
            "Spend per hour by model",
            [("sum by (provider, model) (increase(ventureslab_llm_cost_usd_total[1h]))", "{{provider}} {{model}}")],
            "currencyUSD",
        ),
        ts(
            "Cost per run p50 / p95",
            [
                ("histogram_quantile(0.5, sum by (le) (rate(ventureslab_run_cost_usd_bucket[1h])))", "p50"),
                ("histogram_quantile(0.95, sum by (le) (rate(ventureslab_run_cost_usd_bucket[1h])))", "p95"),
            ],
            "currencyUSD",
        ),
        ts(
            "Tokens by model",
            [("sum by (model, direction) (rate(ventureslab_llm_tokens_total[10m]))", "{{model}} {{direction}}")],
            "short",
            w=24,
        ),
        ts(
            "Calls by model (fallback share)",
            [
                (
                    'sum by (provider, model) (rate(ventureslab_llm_calls_total{outcome="ok"}[10m]))',
                    "{{provider}} {{model}}",
                )
            ],
            "ops",
        ),
        ts(
            "Cache lookups",
            [("sum by (namespace, result) (rate(ventureslab_cache_total[10m]))", "{{namespace}} {{result}}")],
            "ops",
        ),
    ],
)

CX = dashboard(
    "vl-cx",
    "VenturesLab · Customer experience",
    "Usage, success, satisfaction and report quality.",
    [
        stat("Runs (24h)", f"sum(increase(ventureslab_runs_total{{{X}}}[24h]))", "short", decimals=0),
        stat(
            "Complete reports (24h)",
            '1 - (sum(increase(ventureslab_runs_total{status="failed"}[24h])) + (sum(increase(ventureslab_runs_degraded_total[24h])) or vector(0))) / clamp_min(sum(increase(ventureslab_runs_total[24h])), 1)',
            "percentunit",
            decimals=0,
            thresholds=steps((None, "red"), (0.9, "orange"), (0.95, "green")),
        ),
        stat(
            "Satisfaction 👍 share (7d)",
            'sum(increase(ventureslab_feedback_total{rating="up"}[7d])) / clamp_min(sum(increase(ventureslab_feedback_total{rating=~"up|down"}[7d])), 1)',
            "percentunit",
            decimals=0,
            thresholds=steps((None, "red"), (0.6, "orange"), (0.8, "green")),
        ),
        stat("Corrections (7d)", 'sum(increase(ventureslab_feedback_total{rating="correction"}[7d])) + 0', "short"),
        ts("Runs by source", [(f"sum by (source) (increase(ventureslab_runs_total{{{X}}}[1h]))", "{{source}}")]),
        ts("Feedback", [("sum by (rating) (increase(ventureslab_feedback_total[1h]))", "{{rating}}")]),
        ts(
            "Quality-check pass rate by agent",
            [
                (
                    'sum by (agent) (rate(ventureslab_quality_checks_total{passed="true"}[1h])) / clamp_min(sum by (agent) (rate(ventureslab_quality_checks_total[1h])), 1e-9)',
                    "{{agent}}",
                )
            ],
            "percentunit",
            w=24,
        ),
        ts(
            "Manipulated inputs removed",
            [("sum by (kind) (increase(ventureslab_injection_findings_total[1h]))", "{{kind}}")],
        ),
        ts(
            "Median time to report",
            [
                (
                    "histogram_quantile(0.5, sum by (le, source) (rate(ventureslab_run_duration_seconds_bucket[1h])))",
                    "{{source}}",
                )
            ],
            "s",
        ),
    ],
)

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for d in (OPS, COST, CX):
        (OUT / f"{d['uid']}.json").write_text(json.dumps(d, indent=1) + "\n")
        print("wrote", d["uid"], len(d["panels"]), "panels")
