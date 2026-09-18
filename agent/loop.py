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

import inspect
import json
from typing import Any, Protocol

from agent.tools import (
    get_flagged_returns,
    get_operations_summary,
    get_quarantined_rows,
    get_unmatched_returns,
    propose_action,
)
from config import get_settings
from observability.trace import Trace, TraceStep, timer

# The tools the agent may call, by name. Each takes no arguments and returns JSON-safe
# data. This dict is the single source of truth for both the schemas sent to Claude and
# the dispatch when Claude asks for a call.
_TOOL_FUNCTIONS = {
    "get_operations_summary": get_operations_summary,
    "get_unmatched_returns": get_unmatched_returns,
    "get_flagged_returns": get_flagged_returns,
    "get_quarantined_rows": get_quarantined_rows,
    "propose_action": propose_action,
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
    {
        "name": "propose_action",
        "description": (
            "Propose a write action for a human to confirm, and return whether the "
            "guardrail policy allows it, with a reason. This never commits: an allowed "
            "proposal still needs a separate human confirmation step. Use when asked to "
            "confirm an order, hold an account, or issue a refund note."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["confirm_order", "hold_account", "issue_refund_note"],
                    "description": "which write action to propose",
                },
                "order_id": {
                    "type": "integer",
                    "description": "the order the action targets",
                },
                "rationale": {
                    "type": "string",
                    "description": "why the action is proposed; a human reads this",
                },
                "supporting_return_row": {
                    "type": "integer",
                    "description": (
                        "the matched return that justifies a refund note; required for "
                        "issue_refund_note"
                    ),
                },
            },
            "required": ["action", "order_id", "rationale"],
        },
    },
]

# What each tool is allowed to receive, derived from the schemas above so the two cannot
# drift. A tool registered without a schema gets nothing, which fails loudly on a tool
# that needs arguments rather than quietly passing unvetted input.
_TOOL_INPUT_KEYS: dict[str, set[str]] = {
    schema["name"]: set(schema["input_schema"].get("properties", {}))
    for schema in _TOOL_SCHEMAS
}

_SYSTEM_PROMPT = (
    "You are an operations assistant for OpsBridge. Answer questions about the "
    "reconciled orders and returns by calling the provided tools and summarizing their "
    "results in plain language. Only state facts the tools return; do not invent "
    "numbers. If a tool returns an empty list, say so plainly."
)

_MAX_ITERATIONS = 8

# Returned when the iteration guard stops the loop. Named so a caller can tell it apart
# from a real answer the model wrote.
ITERATION_LIMIT_MESSAGE = (
    "Stopped after the maximum number of tool-use iterations without a final answer."
)


class AnthropicLike(Protocol):
    """The slice of the Anthropic client this loop uses, so a stub can satisfy it."""

    @property
    def messages(self) -> Any: ...


def _build_real_client() -> Any:
    from anthropic import Anthropic

    return Anthropic(api_key=get_settings().anthropic_api_key)


def _dispatch_tool(name: str, tool_input: dict[str, Any]) -> Any:
    """Run the named tool with the input Claude supplied and return its result.

    The input is filtered to the keys the tool's schema declares, so the model can only
    reach parameters that were deliberately exposed. The tool functions take more than
    they advertise (database_url and returns_csv exist for tests), and those stay
    unreachable. The read tools declare nothing, so Claude sends an empty object and
    func(**{}) is just func(); propose_action takes its proposal fields this way.

    A call the tool's signature will not accept, such as a required argument the model left
    out, comes back as an error dict like an unknown tool name does, so the model can read
    it and try again instead of the turn ending on an exception. That check is a signature
    bind, not a try around the call, so a TypeError raised inside a tool stays a crash and
    is not disguised as a bad call.
    """
    func = _TOOL_FUNCTIONS.get(name)
    if func is None:
        return {"error": f"unknown tool: {name}"}
    allowed_keys = _TOOL_INPUT_KEYS.get(name, set())
    filtered_input = {
        key: value for key, value in tool_input.items() if key in allowed_keys
    }
    try:
        inspect.signature(func).bind(**filtered_input)
    except TypeError as exc:
        return {
            "error": (
                f"invalid arguments for tool {name}: {exc}. "
                f"Supplied: {sorted(filtered_input)}."
            )
        }
    return func(**filtered_input)


def run_turn(
    messages: list[dict[str, Any]],
    client: AnthropicLike | None = None,
    trace: Trace | None = None,
) -> str:
    """
    Run one tool-calling turn against a caller-supplied message list and return the answer.

    The message list is mutated in place: the assistant tool-use turns and the tool_result
    turns are appended as the loop runs, so a caller that keeps the list has the full
    context, tool calls included. The final response is not appended; a caller that keeps
    the list appends the returned text itself. Runs until a final text answer or the
    iteration guard, which returns ITERATION_LIMIT_MESSAGE instead. The client is injected
    for testability and defaults to a real one.

    Pass a Trace to record what the turn did: one step per model call and one per tool
    call, plus the final answer. Tracing only observes. It never changes the control flow
    or the returned string, and without a trace the loop runs exactly as before. This
    fills the Trace object and nothing else: persistence belongs to the caller, so the
    loop opens no database connection of its own.
    """
    active_client = client or _build_real_client()
    model = get_settings().anthropic_model

    for _ in range(_MAX_ITERATIONS):
        with timer() as model_timer:
            response = active_client.messages.create(
                model=model,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=_TOOL_SCHEMAS,
                messages=messages,
            )
        if trace is not None:
            trace.add_step(
                TraceStep(
                    kind="model_call",
                    name=model,
                    input={"messages": len(messages)},
                    output={"stop_reason": response.stop_reason},
                    latency_ms=model_timer.elapsed_ms,
                )
            )

        if response.stop_reason != "tool_use":
            answer = "".join(
                block.text for block in response.content if block.type == "text"
            ).strip()
            if trace is not None:
                trace.final_answer = answer
            return answer

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                try:
                    with timer() as tool_timer:
                        result = _dispatch_tool(block.name, block.input)
                except Exception as exc:
                    # record the call that failed, then let it fail: the turn that raised
                    # is the one worth inspecting, and swallowing it would hide a bug
                    if trace is not None:
                        trace.add_step(
                            TraceStep(
                                kind="tool_call",
                                name=block.name,
                                input=dict(block.input),
                                output=None,
                                latency_ms=tool_timer.elapsed_ms,
                                error=str(exc),
                            )
                        )
                    raise
                if trace is not None:
                    trace.add_step(
                        TraceStep(
                            kind="tool_call",
                            name=block.name,
                            input=dict(block.input),
                            output=result,
                            latency_ms=tool_timer.elapsed_ms,
                            error=(
                                result["error"]
                                if isinstance(result, dict) and "error" in result
                                else None
                            ),
                        )
                    )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
        messages.append({"role": "user", "content": tool_results})

    if trace is not None:
        trace.final_answer = ITERATION_LIMIT_MESSAGE
    return ITERATION_LIMIT_MESSAGE


def ask(question: str, client: AnthropicLike | None = None) -> str:
    """
    Answer a single natural-language question, with no conversation history.

    A thin wrapper over run_turn for one-off queries: it starts a fresh message list with
    just this question. For multi-turn sessions, use agent.conversation.Conversation.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    return run_turn(messages, client=client)
