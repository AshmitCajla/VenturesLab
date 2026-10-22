"""Tracing, metrics and structured logs, all keyed by one run id.

* **Traces**: OpenTelemetry spans for run -> agent -> LLM call / tool call. With
  OTEL_EXPORTER_OTLP_ENDPOINT set they are exported (e.g. to Jaeger); otherwise
  the API's no-op tracer is used and spans cost nothing.
* **Metrics**: Prometheus counters and histograms served at ``/metrics``. The
  SLO rules in ``deploy/prometheus/rules.yml`` are built on these names, so rename with care.
* **Logs**: JSON lines (LOG_JSON=1) carrying ``run_id`` and ``trace_id``.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager

from opentelemetry import trace
from prometheus_client import Counter, Gauge, Histogram

from core.config import get_settings
from core.runctx import current_run_id

tracer = trace.get_tracer("ventureslab")

# ------------------------------------------------------------------ metrics
# Buckets cover a full run (minutes) and a single call (seconds).
RUN_BUCKETS = (10, 30, 60, 120, 180, 300, 450, 600, 900, 1800)
CALL_BUCKETS = (0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)

RUNS = Counter("ventureslab_runs_total", "Finished runs", ["status", "source"])
RUN_DURATION = Histogram("ventureslab_run_duration_seconds", "End-to-end run time", ["source"],
                         buckets=RUN_BUCKETS)
RUN_COST = Histogram("ventureslab_run_cost_usd", "Estimated LLM cost per run",
                     buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1))
RUNS_DEGRADED = Counter("ventureslab_runs_degraded_total", "Finished runs with at least one failed agent",
                        ["source"])
RUNS_ACTIVE = Gauge("ventureslab_runs_active", "Runs currently executing")
RUNS_QUEUED = Gauge("ventureslab_runs_queued", "Runs waiting for a slot")
AGENT_RUNS = Counter("ventureslab_agent_runs_total", "Agent executions", ["agent", "status"])
AGENT_DURATION = Histogram("ventureslab_agent_duration_seconds", "Agent latency", ["agent"],
                           buckets=CALL_BUCKETS)
LLM_CALLS = Counter("ventureslab_llm_calls_total", "LLM calls", ["provider", "model", "outcome"])
LLM_LATENCY = Histogram("ventureslab_llm_latency_seconds", "LLM call latency", ["provider", "model"],
                        buckets=CALL_BUCKETS)
LLM_TOKENS = Counter("ventureslab_llm_tokens_total", "LLM tokens", ["provider", "model", "direction"])
LLM_COST = Counter("ventureslab_llm_cost_usd_total", "Estimated LLM spend", ["provider", "model"])
TOOL_CALLS = Counter("ventureslab_tool_calls_total", "Tool calls", ["tool", "outcome"])
CHECKS = Counter("ventureslab_quality_checks_total", "Quality check results", ["agent", "passed"])
INJECTIONS = Counter("ventureslab_injection_findings_total", "Manipulation removed from inputs", ["kind"])
FEEDBACK = Counter("ventureslab_feedback_total", "Analyst feedback on reports", ["rating"])
DECK_FAILURES = Counter("ventureslab_deck_parse_failures_total", "Uploaded decks that could not be read")
BUDGET_SKIPS = Counter("ventureslab_budget_skips_total", "Agents skipped to stay within budget", ["agent"])


@contextmanager
def span(name: str, **attrs):
    """An OTel span that also carries the run id."""
    run_id = current_run_id()
    with tracer.start_as_current_span(name) as s:
        if run_id:
            s.set_attribute("ventureslab.run_id", run_id)
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
        yield s


@contextmanager
def timed_tool(tool: str):
    started = time.perf_counter()
    with span(f"tool.{tool}", **{"tool.name": tool}) as s:
        try:
            yield s
        except Exception:
            TOOL_CALLS.labels(tool, "error").inc()
            raise
        else:
            TOOL_CALLS.labels(tool, "ok").inc()
        finally:
            s.set_attribute("duration_ms", round((time.perf_counter() - started) * 1000))


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


# --------------------------------------------------------------------- logs
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(record.created, 3), "level": record.levelname, "logger": record.name,
               "msg": record.getMessage(), "run_id": current_run_id(), "trace_id": current_trace_id()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class RunIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = current_run_id() or "-"
        return True


_configured = False


def setup(service: str = "ventureslab-api") -> None:
    """Configure logging and (when an endpoint is set) trace export. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True
    s = get_settings()
    handler = logging.StreamHandler()
    handler.addFilter(RunIdFilter())
    handler.setFormatter(JsonFormatter() if s.log_json else logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [run=%(run_id)s]: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    if s.log_dir:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        Path(s.log_dir).mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(Path(s.log_dir) / "ventureslab.jsonl", maxBytes=20_000_000, backupCount=3)
        fh.setFormatter(JsonFormatter())
        fh.setLevel(logging.INFO)
        root.addHandler(fh)

    if s.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=s.otel_endpoint.rstrip("/") + "/v1/traces")))
        trace.set_tracer_provider(provider)
