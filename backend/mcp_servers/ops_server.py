"""MCP server: observability tools for the ops agent (and for you, from Claude Desktop).

    python -m mcp_servers.ops_server      # stdio

Add to Claude Desktop's config to ask "what broke last night?" yourself.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ops import tools

server = MCPServer(
    "ventureslab-ops",
    instructions=(
        "Observability for the VenturesLab analysis service: SLOs, failed runs grouped by error signature, "
        "per-agent health, structured logs, Prometheus, Jaeger traces and runbooks."
    ),
)

for fn in (
    tools.slo_status,
    tools.recent_runs,
    tools.error_signatures,
    tools.agent_health,
    tools.recent_logs,
    tools.query_prometheus,
    tools.get_trace,
    tools.model_health,
    tools.list_runbooks,
    tools.read_runbook,
    tools.incident_history,
):
    server.add_tool(fn)


if __name__ == "__main__":
    server.run("stdio")
