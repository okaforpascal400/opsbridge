# mcp_server/server.py
"""
MCP server exposing the OpsBridge read tools over the Model Context Protocol.

This is a thin protocol adapter, not new logic. It registers the four existing tool
functions from agent/tools.py as MCP tools so any MCP client (Claude Desktop, an IDE
agent, another orchestrator) can discover and call them over the standard stdio
transport. The tools themselves are unchanged and already tested in test_tools.py.

The adapter does two protocol-level things and nothing else. It wraps each list result in
one JSON object, because FastMCP v1 sends a bare list as one text block per item and an
empty list as no text at all, so a client reading the text would see nothing where the
answer is "none". And it runs each tool in a worker thread, because FastMCP v1 calls a sync
tool directly on the event loop, where a slow database connect would stop the server from
answering anything else.

Pinned to the mcp SDK v1 line (mcp==1.30.0) and its FastMCP high-level API. The v2 SDK
released 2026-07-28 renamed FastMCP to MCPServer and removed mcp.server.fastmcp, a
breaking change, so the dependency is pinned to 1.x deliberately (see DECISIONS).

Run it from the repo root as a stdio MCP server with:
    python -m mcp_server.server
An MCP client does not start servers in the repo root and does not pass your shell's
environment through, so its config must give the absolute path to the venv python as the
command, ["-m", "mcp_server.server"] as the args, and the repo root as the working
directory. Settings reads .env from the working directory, so without that the repo's .env
is ignored.
"""
from __future__ import annotations

import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent.tools import (
    get_flagged_returns,
    get_operations_summary,
    get_quarantined_rows,
    get_unmatched_returns,
)

mcp = FastMCP("opsbridge")


@mcp.tool()
async def operations_summary() -> dict[str, Any]:
    """Aggregate reconciliation picture: order/return counts, quarantines, and the
    high/low/none breakdown of return matches. Use for overview questions."""
    return await asyncio.to_thread(get_operations_summary)


@mcp.tool()
async def unmatched_returns() -> dict[str, Any]:
    """Returns that found no plausible order match (confidence NONE), each with its
    score and rationale. Use when asked which returns could not be tied to an order."""
    return {"unmatched_returns": await asyncio.to_thread(get_unmatched_returns)}


@mcp.tool()
async def flagged_returns() -> dict[str, Any]:
    """Returns that matched only at LOW confidence and need human review, each with the
    candidate order, score, and rationale. Use when asked what needs review."""
    return {"flagged_returns": await asyncio.to_thread(get_flagged_returns)}


@mcp.tool()
async def quarantined_rows() -> dict[str, Any]:
    """Source rows that could not be canonicalized, each with the raw row and the reason
    it failed. Use when asked what data could not be processed and why."""
    return {"quarantined_rows": await asyncio.to_thread(get_quarantined_rows)}


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
