# tests/test_conversation.py
"""
Multi-turn Conversation tests, using a stubbed model.

Like the loop tests, these never hit the live API. A scripted fake client returns
responses in order across turns, so the tests verify the session MECHANICS: that history
accumulates across turns, that a later turn sees the earlier exchanges (tool calls
included), that each ask returns the assistant's final text, and that a failed or empty
turn leaves the history untouched. The tool wiring itself is covered in test_loop.py;
here the focus is on state carried between turns.

These pass persist_traces=False so a stubbed turn never reaches a database; the
trace that a real turn saves is covered by the gated test at the end of this file.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine

import agent.loop as loop_module
from agent.conversation import Conversation
from observability.trace import load_trace


def _text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, block_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, id=block_id, input={})


def _response(stop_reason: str, content: list) -> SimpleNamespace:
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class _ScriptedClient:
    """A fake client returning pre-scripted responses in order, recording each call.

    A scripted exception is raised instead of returned, to simulate a failed API call.
    """

    def __init__(self, scripted_responses: list[SimpleNamespace | Exception]) -> None:
        self._responses = list(scripted_responses)
        self.calls: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs: Any) -> SimpleNamespace:
        # copy the list: it keeps growing after this call returns, so the live reference
        # would show later turns instead of what was actually sent
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _sent_contents(call: dict[str, Any]) -> list[Any]:
    return [m["content"] for m in call["messages"]]


def test_single_turn_returns_the_answer():
    client = _ScriptedClient([_response("end_turn", [_text_block("First answer.")])])
    convo = Conversation(client=client, persist_traces=False)
    assert convo.ask("first question") == "First answer."


def test_history_accumulates_across_turns():
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("First answer.")]),
            _response("end_turn", [_text_block("Second answer.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("first question")
    convo.ask("second question")

    # after two turns the history holds both questions and both answers, in order
    contents = [m["content"] for m in convo.history]
    assert contents == [
        "first question",
        "First answer.",
        "second question",
        "Second answer.",
    ]


def test_second_turn_sees_the_first_exchange():
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("First answer.")]),
            _response("end_turn", [_text_block("Second answer.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("first question")
    convo.ask("second question")

    # each call carries exactly the history up to and including its own question
    assert _sent_contents(client.calls[0]) == ["first question"]
    assert _sent_contents(client.calls[1]) == [
        "first question",
        "First answer.",
        "second question",
    ]


def test_turn_count_tracks_questions():
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("A.")]),
            _response("end_turn", [_text_block("B.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    assert convo.turn_count == 0
    convo.ask("one")
    assert convo.turn_count == 1
    convo.ask("two")
    assert convo.turn_count == 2


def test_tool_turns_are_kept_in_history(monkeypatch):
    monkeypatch.setattr(loop_module, "_dispatch_tool", lambda _name, _input: {"flagged": 1})
    tool_use = _tool_use_block("get_flagged_returns", "tu_1")
    client = _ScriptedClient(
        [
            _response("tool_use", [tool_use]),
            _response("end_turn", [_text_block("One return needs review.")]),
            _response("end_turn", [_text_block("Its name did not match.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("what needs review?")
    convo.ask("why?")

    # the follow-up carries the earlier tool call and its result, not just the Q and A
    sent = client.calls[2]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user", "assistant", "user"]
    assert sent[1]["content"] == [tool_use]
    assert sent[2]["content"][0]["tool_use_id"] == "tu_1"
    assert sent[3]["content"] == "One return needs review."
    assert sent[4]["content"] == "why?"
    assert convo.history == [*sent, {"role": "assistant", "content": "Its name did not match."}]
    # tool_result turns are user messages but not questions
    assert convo.turn_count == 2


def test_failed_tool_leaves_history_unchanged(monkeypatch):
    def _database_down(_name: str, _input: dict) -> Any:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(loop_module, "_dispatch_tool", _database_down)
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("First answer.")]),
            _response("tool_use", [_tool_use_block("get_operations_summary", "tu_1")]),
            _response("end_turn", [_text_block("Recovered.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("first question")
    before = convo.history

    with pytest.raises(RuntimeError, match="database unavailable"):
        convo.ask("how many orders?")

    # earlier turns survive and no orphaned tool_use is left, so the next request is valid
    assert convo.history == before
    assert convo.turn_count == 1
    assert convo.ask("try again") == "Recovered."
    assert _sent_contents(client.calls[2]) == ["first question", "First answer.", "try again"]


def test_failed_model_call_leaves_history_unchanged():
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("First answer.")]),
            ConnectionError("model unreachable"),
            _response("end_turn", [_text_block("Recovered.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("first question")
    before = convo.history

    with pytest.raises(ConnectionError, match="model unreachable"):
        convo.ask("how many orders?")

    assert convo.history == before
    assert convo.turn_count == 1
    assert convo.ask("try again") == "Recovered."
    assert _sent_contents(client.calls[2]) == ["first question", "First answer.", "try again"]


def test_empty_answer_is_returned_but_not_recorded():
    # the API rejects an empty assistant message anywhere but last, so keeping one
    # would make every later turn fail
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("First answer.")]),
            _response("end_turn", []),
            _response("end_turn", [_text_block("Third answer.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("first question")
    before = convo.history

    assert convo.ask("second question") == ""
    assert convo.history == before
    convo.ask("third question")
    assert _sent_contents(client.calls[2]) == [
        "first question",
        "First answer.",
        "third question",
    ]


def test_iteration_limit_notice_is_returned_but_not_recorded(monkeypatch):
    # the notice comes from the loop, not the model, so it must not enter the history
    # as an assistant message, and the unfinished tool turns are dropped with it
    monkeypatch.setattr(loop_module, "_dispatch_tool", lambda _name, _input: {"ok": True})
    runaway = [
        _response("tool_use", [_tool_use_block("get_operations_summary", f"tu_{i}")])
        for i in range(loop_module._MAX_ITERATIONS)
    ]
    client = _ScriptedClient(
        [
            _response("end_turn", [_text_block("First answer.")]),
            *runaway,
            _response("end_turn", [_text_block("Done.")]),
        ]
    )
    convo = Conversation(client=client, persist_traces=False)
    convo.ask("first question")
    before = convo.history

    assert convo.ask("loop forever") == loop_module.ITERATION_LIMIT_MESSAGE
    assert convo.history == before
    assert convo.turn_count == 1
    convo.ask("next question")
    assert _sent_contents(client.calls[-1]) == [
        "first question",
        "First answer.",
        "next question",
    ]


# --- the traced turn, persisted (gated on a throwaway database) ----------------------


def _test_engine():
    url = os.environ.get("OPSBRIDGE_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("OPSBRIDGE_TEST_DATABASE_URL is not set; skipping trace persistence test.")
    return create_engine(url)


def test_a_turn_saves_a_trace_that_reads_back(monkeypatch):
    monkeypatch.setattr(loop_module, "_dispatch_tool", lambda _name, _input: {"flagged": 1})
    engine = _test_engine()
    client = _ScriptedClient(
        [
            _response("tool_use", [_tool_use_block("get_flagged_returns", "tu_1")]),
            _response("end_turn", [_text_block("One return needs review.")]),
        ]
    )
    convo = Conversation(client=client, engine=engine)

    answer = convo.ask("what needs review?")

    assert answer == "One return needs review."
    assert convo.last_trace_id is not None
    saved = load_trace(convo.last_trace_id, engine)
    assert saved is not None
    assert saved["question"] == "what needs review?"
    assert saved["final_answer"] == "One return needs review."
    assert [step["kind"] for step in saved["steps"]] == [
        "model_call",
        "tool_call",
        "model_call",
    ]
    assert saved["total_ms"] >= 0
    engine.dispose()


def test_a_failed_turn_is_still_traced():
    # an empty answer is not recorded in history, but it is exactly the turn worth reading
    # back, so the trace is saved anyway
    engine = _test_engine()
    client = _ScriptedClient([_response("end_turn", [])])
    convo = Conversation(client=client, engine=engine)

    assert convo.ask("a question with no answer") == ""
    assert convo.history == []

    saved = load_trace(convo.last_trace_id, engine)
    assert saved is not None
    assert saved["question"] == "a question with no answer"
    assert saved["final_answer"] == ""
    engine.dispose()


def test_persistence_off_means_no_database_and_still_a_trace_id():
    # what the stubbed tests rely on: a trace id is still assigned, nothing is written
    client = _ScriptedClient([_response("end_turn", [_text_block("Fine.")])])
    convo = Conversation(client=client, persist_traces=False)

    assert convo.ask("status?") == "Fine."
    assert convo.last_trace_id is not None


def test_a_raising_turn_still_persists_its_trace(monkeypatch):
    # a crash is the turn most worth reading back, so the trace is saved on the way out
    def _database_down(_name: str, _input: dict) -> Any:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(loop_module, "_dispatch_tool", _database_down)
    engine = _test_engine()
    client = _ScriptedClient(
        [_response("tool_use", [_tool_use_block("get_operations_summary", "tu_1")])]
    )
    convo = Conversation(client=client, engine=engine)

    with pytest.raises(RuntimeError, match="database unavailable"):
        convo.ask("how many orders?")

    assert convo.history == []
    saved = load_trace(convo.last_trace_id, engine)
    assert saved is not None
    assert saved["question"] == "how many orders?"
    assert saved["final_answer"] == ""
    failing_step = saved["steps"][-1]
    assert failing_step["kind"] == "tool_call"
    assert failing_step["error"] == "database unavailable"
    engine.dispose()


def test_a_save_failure_does_not_break_the_turn(monkeypatch):
    # losing a trace is acceptable; losing an answer the agent already produced is not
    def _save_explodes(_trace, _engine):
        raise RuntimeError("traces table is gone")

    monkeypatch.setattr("agent.conversation.save_trace", _save_explodes)
    client = _ScriptedClient([_response("end_turn", [_text_block("Still answered.")])])
    # a stand-in engine: the stubbed save never reaches a database
    convo = Conversation(client=client, engine=object())

    assert convo.ask("status?") == "Still answered."
    assert convo.history[-1]["content"] == "Still answered."
    assert convo.last_trace_id is not None
