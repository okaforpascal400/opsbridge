# agent/loop.py
"""
The tool-calling agent loop.

This is where OpsBridge stops being deterministic plumbing and becomes an AI system: a
natural-language question goes in, Claude decides which read tools to call, this loop
runs the real tool functions and feeds the results back, and Claude composes the answer.

The agent has no special knowledge. It only knows the tools exist and what they return;
all trustworthy logic lives below it in the pipeline and the reconciliation layer. The
model is an orchestrator that turns a question into the right sequence of tool calls, not
a source of truth.

The Anthropic client is injected (defaults to a real one) so the loop can be tested with
a stub, no network, deterministic, free in CI.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from agent.tools import (
    get_flagged_returns,
    get_operations_summary,
    get_quarantined_rows,
    get_unmatched_returns,
)
from config import get_settings

# The tools the agent may call, by name. Each takes no arguments and returns JSON-safe
# data. This dict is the single source of truth for both the schemas sent to Claude and
# the dispatch when Claude asks for a call.
_TOOL_FUNCTIONS = {
    "get_operations_summary": get_operations_summary,
    "get_unmatched_returns": get_unmatched_returns,
    "get_flagged_returns": get_flagged_returns,
    "get_quarantined_rows": get_quarantined_rows,
}

_TOOL_SCHEMAS = [
    {
        "name": "get_operations_summary",
        "description": (
            "Return the aggregate reconciliation picture: how many orders and returns "
            "came in, how many canonicalized, how many were quarantined, and the "
            "high/low/none breakdown of return matches. Use for overview questions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_unmatched_returns",
        "description": (
            "Return the returns that found no plausible order match (confidence NONE), "
            "each with its score and rationale. Use when asked which returns could not "
            "be tied to an order."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_flagged_returns",
        "description": (
            "Return the returns that matched only at LOW confidence and need human "
            "review, each with the candidate order, score, and rationale. Use when asked "
            "what needs review or which matches are uncertain."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_quarantined_rows",
        "description": (
            "Return the source rows that could not be canonicalized, each with the raw "
            "row and the reason it failed. Use when asked what data could not be "
            "processed and why."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

_SYSTEM_PROMPT = (
    "You are an operations assistant for OpsBridge. Answer questions about the "
    "reconciled orders and returns by calling the provided tools and summarizing their "
    "results in plain language. Only state facts the tools return; do not invent "
    "numbers. If a tool returns an empty list, say so plainly."
)

_MAX_ITERATIONS = 8


class _AnthropicLike(Protocol):
    """The slice of the Anthropic client this loop uses, so a stub can satisfy it."""

    @property
    def messages(self) -> Any: ...


def _build_real_client() -> Any:
    from anthropic import Anthropic

    return Anthropic(api_key=get_settings().anthropic_api_key)


def _dispatch_tool(name: str) -> Any:
    """Run the named tool against the configured sources and return its result."""
    func = _TOOL_FUNCTIONS.get(name)
    if func is None:
        return {"error": f"unknown tool: {name}"}
    return func()


def ask(question: str, client: _AnthropicLike | None = None) -> str:
    """
    Answer a natural-language operations question by letting Claude call the read tools.

    Runs the tool-use loop until the model returns a final text answer or the iteration
    guard is hit. The client is injected for testability and defaults to a real one.
    """
    active_client = client or _build_real_client()
    model = get_settings().anthropic_model

    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

    for _ in range(_MAX_ITERATIONS):
        response = active_client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=_TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            # final answer: collect any text blocks and return them
            return "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()

        # the model asked for one or more tool calls; run them and feed results back
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = _dispatch_tool(block.name)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
        messages.append({"role": "user", "content": tool_results})

    return "Stopped after the maximum number of tool-use iterations without a final answer."
