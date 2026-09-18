# tests/test_loop.py
"""
Agent loop tests, using a stubbed model.

The loop calls a real external API in production, which is non-deterministic and costs
money, so it is never exercised against the live API here. Instead a fake client returns
scripted responses, letting these tests verify the loop MECHANICS deterministically and
for free: that a tool-use response triggers the real tool and feeds its result back, that
a final answer is returned as text, and that the iteration guard stops a runaway loop.

The tools themselves are covered by test_tools.py; here we only care that the loop wires
Claude's tool requests to the tool functions correctly.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import agent.loop as loop_module
from agent.loop import ask


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, block_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, id=block_id, input={})


def _response(stop_reason: str, content: list) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class _ScriptedClient:
    """A fake Anthropic client that returns pre-scripted responses in order.

    It records every messages.create call so tests can assert what the loop sent back,
    including that tool results were fed to the model.
    """

    def __init__(self, scripted_responses: list[SimpleNamespace]) -> None:
        self._responses = list(scripted_responses)
        self.calls: list[dict[str, Any]] = []
        self.messages = self  # so `client.messages.create(...)` reaches create()

    def create(self, **kwargs: Any) -> SimpleNamespace:
        # copy the list: the loop keeps appending to it after this call returns, so the
        # live reference would show later turns instead of what was actually sent
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self._responses.pop(0)


def test_final_answer_with_no_tool_call_is_returned_directly():
    client = _ScriptedClient([_response("end_turn", [_text_block("Hello, ops.")])])
    answer = ask("hi", client=client)
    assert answer == "Hello, ops."
    assert len(client.calls) == 1
    assert client.calls[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_ask_starts_fresh_each_call():
    # ask() is one-shot: a second question must not carry the first exchange along
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("A.")]),
            _response("end_turn", [_text_block("B.")]),
        ]
    )
    ask("first", client=client)
    ask("second", client=client)
    assert client.calls[1]["messages"] == [{"role": "user", "content": "second"}]


def test_tool_use_triggers_the_tool_then_returns_the_final_answer(monkeypatch):
    # stub the dispatched tool so the loop does not hit the database in this unit test
    import agent.loop as loop_module

    monkeypatch.setattr(
        loop_module,
        "_dispatch_tool",
        lambda name, _input: {"orders_in": 200, "tool_called": name},
    )

    client = _ScriptedClient(
        [
            _response("tool_use", [_tool_use_block("get_operations_summary", "tu_1")]),
            _response("end_turn", [_text_block("There are 200 orders.")]),
        ]
    )

    answer = ask("how many orders?", client=client)
    assert answer == "There are 200 orders."
    # the loop made two model calls: the tool request, then the follow-up with results
    assert len(client.calls) == 2
    # the second call must carry the tool_result back to the model
    second_call_messages = client.calls[1]["messages"]
    def _has_tool_result(message: dict) -> bool:
        content = message["content"]
        if not isinstance(content, list):
            return False
        return any(
            isinstance(part, dict) and part.get("type") == "tool_result"
            for part in content
        )

    assert any(_has_tool_result(m) for m in second_call_messages)
    assert [m["role"] for m in second_call_messages] == ["user", "assistant", "user"]
    assert second_call_messages[2]["content"][0]["tool_use_id"] == "tu_1"


def test_iteration_guard_stops_a_runaway_tool_loop(monkeypatch):
    import agent.loop as loop_module

    monkeypatch.setattr(loop_module, "_dispatch_tool", lambda _name, _input: {"ok": True})

    # a client that ALWAYS asks for another tool call, never finishing
    class _NeverStops:
        def __init__(self) -> None:
            self.messages = self
            self.calls = 0

        def create(self, **_kwargs: Any) -> SimpleNamespace:
            self.calls += 1
            block = _tool_use_block("get_operations_summary", f"tu_{self.calls}")
            return _response("tool_use", [block])

    client = _NeverStops()
    answer = ask("loop forever", client=client)
    assert "maximum number of tool-use iterations" in answer
    assert client.calls == loop_module._MAX_ITERATIONS


def test_unknown_tool_name_is_handled_gracefully(monkeypatch):
    # if the model names a tool that does not exist, dispatch returns an error dict
    # rather than raising, and the loop can still finish
    client = _ScriptedClient(
        [
            _response("tool_use", [_tool_use_block("nonexistent_tool", "tu_1")]),
            _response("end_turn", [_text_block("Sorry, I could not do that.")]),
        ]
    )
    answer = ask("call a bad tool", client=client)
    assert answer == "Sorry, I could not do that."


def test_dispatch_forwards_tool_input_as_keyword_arguments(monkeypatch):
    # propose_action takes its proposal fields from the model's tool input, so whatever
    # Claude puts in block.input has to arrive as kwargs on the tool function
    captured = {}

    def _fake_tool(**kwargs):
        captured.update(kwargs)
        return {"allowed": True, "reason": "stub"}

    monkeypatch.setitem(loop_module._TOOL_FUNCTIONS, "propose_action", _fake_tool)
    result = loop_module._dispatch_tool(
        "propose_action",
        {"action": "confirm_order", "order_id": 5, "rationale": "looks right"},
    )

    assert result == {"allowed": True, "reason": "stub"}
    assert captured == {"action": "confirm_order", "order_id": 5, "rationale": "looks right"}


def test_loop_passes_the_blocks_input_to_the_tool(monkeypatch):
    # the same path end to end: the loop must hand block.input to the dispatcher, not {}
    received = {}

    def _capture(name, tool_input):
        received["name"] = name
        received["input"] = tool_input
        return {"allowed": False, "reason": "stub"}

    monkeypatch.setattr(loop_module, "_dispatch_tool", _capture)
    block = SimpleNamespace(
        type="tool_use",
        name="propose_action",
        id="tu_1",
        input={"action": "hold_account", "order_id": 5, "rationale": "fraud"},
    )
    client = _ScriptedClient(
        [
            _response("tool_use", [block]),
            _response("end_turn", [_text_block("Proposed.")]),
        ]
    )

    assert ask("hold order 5", client=client) == "Proposed."
    assert received["name"] == "propose_action"
    assert received["input"] == {"action": "hold_account", "order_id": 5, "rationale": "fraud"}


def test_read_tools_still_dispatch_with_an_empty_input(monkeypatch):
    # Claude sends {} for the no-argument read tools, and func(**{}) must still work
    monkeypatch.setitem(
        loop_module._TOOL_FUNCTIONS, "get_operations_summary", lambda: {"orders_in": 200}
    )
    assert loop_module._dispatch_tool("get_operations_summary", {}) == {"orders_in": 200}


def test_propose_action_is_registered_with_its_schema():
    # the tool the agent needs for write proposals must be dispatchable and advertised
    assert "propose_action" in loop_module._TOOL_FUNCTIONS
    schema = next(s for s in loop_module._TOOL_SCHEMAS if s["name"] == "propose_action")
    properties = schema["input_schema"]["properties"]
    assert schema["input_schema"]["required"] == ["action", "order_id", "rationale"]
    assert properties["action"]["enum"] == [
        "confirm_order",
        "hold_account",
        "issue_refund_note",
    ]
    assert "supporting_return_row" in properties


def test_undeclared_tool_input_keys_are_stripped(monkeypatch):
    # the model can only reach parameters the schema advertises: propose_action also
    # takes database_url and returns_csv, and those must stay unreachable
    captured = {}

    def _fake_tool(**kwargs):
        captured.update(kwargs)
        return {"allowed": True, "reason": "stub"}

    monkeypatch.setitem(loop_module._TOOL_FUNCTIONS, "propose_action", _fake_tool)
    result = loop_module._dispatch_tool(
        "propose_action",
        {
            "action": "confirm_order",
            "order_id": 5,
            "rationale": "looks right",
            "database_url": "postgresql+psycopg2://attacker/elsewhere",
            "returns_csv": "/tmp/mine.csv",
        },
    )

    assert result == {"allowed": True, "reason": "stub"}
    assert captured == {"action": "confirm_order", "order_id": 5, "rationale": "looks right"}


def test_undeclared_input_is_stripped_through_the_loop(monkeypatch):
    # the same strip on the real path, where the input comes off a tool_use block
    captured = {}

    def _fake_tool(**kwargs):
        captured.update(kwargs)
        return {"allowed": True, "reason": "stub"}

    monkeypatch.setitem(loop_module._TOOL_FUNCTIONS, "propose_action", _fake_tool)
    block = SimpleNamespace(
        type="tool_use",
        name="propose_action",
        id="tu_1",
        input={
            "action": "hold_account",
            "order_id": 5,
            "rationale": "fraud",
            "database_url": "postgresql+psycopg2://attacker/elsewhere",
        },
    )
    client = _ScriptedClient(
        [
            _response("tool_use", [block]),
            _response("end_turn", [_text_block("Proposed.")]),
        ]
    )

    assert ask("hold order 5", client=client) == "Proposed."
    assert "database_url" not in captured
    assert captured == {"action": "hold_account", "order_id": 5, "rationale": "fraud"}


def test_read_tools_reject_any_input_the_model_invents(monkeypatch):
    # the read tools declare no properties, so every key is dropped and the call is func()
    monkeypatch.setitem(
        loop_module._TOOL_FUNCTIONS, "get_operations_summary", lambda: {"orders_in": 200}
    )
    result = loop_module._dispatch_tool(
        "get_operations_summary", {"database_url": "postgresql+psycopg2://attacker/elsewhere"}
    )
    assert result == {"orders_in": 200}


def test_a_call_missing_a_required_argument_returns_an_error_dict():
    # the real propose_action, called without its rationale: Python rejects the call
    # before any database work, and the dispatcher hands the model a readable result
    result = loop_module._dispatch_tool(
        "propose_action", {"action": "confirm_order", "order_id": 5}
    )

    assert "error" in result
    assert "propose_action" in result["error"]
    assert "rationale" in result["error"]


def test_a_bad_tool_call_does_not_end_the_turn():
    # the same path through the loop: the model gets the error back as a tool_result and
    # can answer, instead of the turn dying on a TypeError
    block = SimpleNamespace(
        type="tool_use",
        name="propose_action",
        id="tu_1",
        input={"action": "confirm_order", "order_id": 5},
    )
    client = _ScriptedClient(
        [
            _response("tool_use", [block]),
            _response("end_turn", [_text_block("I need a rationale for that.")]),
        ]
    )

    assert ask("confirm order 5", client=client) == "I need a rationale for that."
    tool_result = client.calls[1]["messages"][2]["content"][0]
    assert "invalid arguments" in tool_result["content"]


def test_a_type_error_inside_a_tool_is_not_swallowed(monkeypatch):
    # only a signature mismatch becomes an error dict: a bug inside a tool must stay a
    # crash, not come back to the model dressed up as a bad call
    def _buggy_tool():
        raise TypeError("'>' not supported between instances of 'NoneType' and 'int'")

    monkeypatch.setitem(loop_module._TOOL_FUNCTIONS, "get_operations_summary", _buggy_tool)

    with pytest.raises(TypeError, match="NoneType"):
        loop_module._dispatch_tool("get_operations_summary", {})
