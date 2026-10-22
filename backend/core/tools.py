"""Tool gateway: agents call tools by name, only the tools they are allowed.

Every agent declares its allow-list (``AgentSpec.tools``). A call outside it is
refused before it reaches the tool, which limits what a manipulated agent could
do. Calls go over MCP to the research server (in-process transport, so no
network hop), are traced and counted, and return the tool's JSON result.
"""
from __future__ import annotations

import json
from typing import Any

from core.telemetry import timed_tool


class ToolNotAllowed(PermissionError):
    pass


class ToolFailed(RuntimeError):
    pass


def decode(res) -> object:
    """A CallToolResult as Python data. Lists and scalars come back as structured_content
    {"result": ...}; dicts as one JSON text block; plain strings as text."""
    sc = getattr(res, "structured_content", None)
    if isinstance(sc, dict) and set(sc) == {"result"}:
        return sc["result"]
    texts = [getattr(c, "text", "") for c in res.content]
    try:
        parsed = [json.loads(t) for t in texts]
    except ValueError:
        return "".join(texts)
    return parsed[0] if len(parsed) == 1 else parsed


async def call(agent: str, allowed: tuple[str, ...], tool: str, args: dict[str, Any]) -> dict:
    if tool not in allowed:
        raise ToolNotAllowed(f"agent '{agent}' is not allowed to call '{tool}' (allowed: {', '.join(allowed)})")
    from mcp import Client

    from mcp_servers.research_server import server

    with timed_tool(tool):
        async with Client(server) as client:
            res = await client.call_tool(tool, args)
    if res.is_error:
        raise ToolFailed(f"{tool}: {''.join(getattr(c, 'text', '') for c in res.content)[:300]}")
    return decode(res) or {}
