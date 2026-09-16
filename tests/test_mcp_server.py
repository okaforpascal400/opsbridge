# tests/test_mcp_server.py
"""
MCP server tests.

The server is a thin adapter that exposes the already-tested tool functions over MCP, so
these tests cover only what the adapter adds: that the four tools are registered and
discoverable with the right names and descriptions, that each tool calls its own
agent/tools.py function, that results arrive as a single JSON text block (including an
empty list), and that the blocking tool work runs off the event loop. The tool behaviour
itself is covered in test_tools.py.

Calls go through a real MCP client session connected to the server in memory, so they see
exactly what a client would, without a subprocess or a database: the tool functions are
stubbed.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult

import mcp_server.server as server_module
from mcp_server.server import mcp

EXPECTED_TOOLS = {
    "operations_summary",
    "unmatched_returns",
    "flagged_returns",
    "quarantined_rows",
}

# each MCP tool and the agent/tools.py function it must call
TOOL_FUNCTIONS = {
    "operations_summary": "get_operations_summary",
    "unmatched_returns": "get_unmatched_returns",
    "flagged_returns": "get_flagged_returns",
    "quarantined_rows": "get_quarantined_rows",
}

LIST_TOOLS = ["unmatched_returns", "flagged_returns", "quarantined_rows"]

# a phrase only the right tool's description should contain, so swapped or misleading
# descriptions fail rather than just empty ones
EXPECTED_DESCRIPTION_TERMS = {
    "operations_summary": "breakdown",
    "unmatched_returns": "confidence NONE",
    "flagged_returns": "LOW confidence",
    "quarantined_rows": "canonicalized",
}


def _registered_tools() -> list:
    return asyncio.run(mcp.list_tools())


def _call(tool_name: str) -> CallToolResult:
    async def _run() -> CallToolResult:
        async with create_connected_server_and_client_session(mcp) as session:
            return await session.call_tool(tool_name, {})

    return asyncio.run(_run())


def _stub_tool_functions(monkeypatch, rows: list[dict[str, Any]] | None = None) -> None:
    """Replace the tool functions the server imported with stubs that name themselves.

    The summary stub returns {"called": <function name>}. Each list stub returns the given
    rows, or by default one row naming itself.
    """
    for function_name in TOOL_FUNCTIONS.values():
        marker = {"called": function_name}
        if function_name == "get_operations_summary":
            result: Any = marker
        else:
            result = [marker] if rows is None else rows
        monkeypatch.setattr(server_module, function_name, lambda result=result: result)


def test_all_four_tools_are_registered():
    names = {tool.name for tool in _registered_tools()}
    assert names >= EXPECTED_TOOLS


def test_no_unexpected_tools_are_registered():
    names = {tool.name for tool in _registered_tools()}
    assert names == EXPECTED_TOOLS


def test_each_description_says_what_its_tool_returns():
    for tool in _registered_tools():
        assert EXPECTED_DESCRIPTION_TERMS[tool.name] in tool.description


def test_server_is_named_opsbridge():
    assert mcp.name == "opsbridge"


@pytest.mark.parametrize(("tool_name", "function_name"), TOOL_FUNCTIONS.items())
def test_each_tool_calls_its_own_function(monkeypatch, tool_name, function_name):
    _stub_tool_functions(monkeypatch)
    result = _call(tool_name)

    assert not result.isError
    marker = {"called": function_name}
    expected = marker if tool_name == "operations_summary" else {tool_name: [marker]}
    assert result.structuredContent == expected


@pytest.mark.parametrize("tool_name", LIST_TOOLS)
def test_empty_list_result_is_one_json_text_block(monkeypatch, tool_name):
    # FastMCP v1 would send a bare empty list as no text at all; the wrapper prevents it
    _stub_tool_functions(monkeypatch, rows=[])
    result = _call(tool_name)

    assert not result.isError
    assert len(result.content) == 1
    assert json.loads(result.content[0].text) == {tool_name: []}
    assert result.structuredContent == {tool_name: []}


def test_list_result_with_rows_is_one_json_text_block(monkeypatch):
    rows = [{"return_row": 1}, {"return_row": 2}]
    _stub_tool_functions(monkeypatch, rows=rows)
    result = _call("flagged_returns")

    assert len(result.content) == 1
    assert json.loads(result.content[0].text) == {"flagged_returns": rows}


def test_tool_work_runs_off_the_event_loop_thread(monkeypatch):
    # a blocking database call on the event loop thread would stall the whole server
    monkeypatch.setattr(
        server_module,
        "get_operations_summary",
        lambda: {"on_event_loop_thread": threading.current_thread() is threading.main_thread()},
    )
    result = _call("operations_summary")
    assert result.structuredContent == {"on_event_loop_thread": False}
