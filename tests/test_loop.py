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
        lambda name: {"orders_in": 200, "tool_called": name},
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

    monkeypatch.setattr(loop_module, "_dispatch_tool", lambda _name: {"ok": True})

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
