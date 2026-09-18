# agent/conversation.py
"""
Multi-turn session state for the agent.

The single-shot ask() in loop.py starts fresh every call, which is right for a one-off
query but wrong for a conversation: real operations use is a back-and-forth ("how many
returns need review?" then "show me the quarantined rows" then "what was wrong with the
first one?"). A Conversation carries the running message history across turns so a
follow-up is interpreted in the context of what came before.

Context assembly is deliberate and visible here: each turn appends the user's question,
then run_turn appends any assistant tool_use turns and user tool_result turns, then the
assistant's final answer text is appended. That whole list, tool calls and results
included, is what the next turn's tool-calling loop sees. Nothing is summarized or
trimmed; the history is the context.

A turn is recorded only when the model finishes it with a non-empty answer. If the model
call or a tool raises, the answer is empty, or the iteration guard stops the loop, the
history is left exactly as it was. A half-finished turn (a tool_use with no tool_result, or
an empty assistant message) would make every later request invalid, so one database outage
would break the session for good; and the guard's notice is not something the model said,
so it must not appear in history as if it were. The same injected-client pattern as
loop.py keeps this testable without the network.

Every turn is traced and the trace is saved, including a turn whose answer was empty or
stopped by the iteration guard: a turn that went wrong is the one worth reading back.
Tracing is additive, so what ask() returns and what it keeps in history are exactly what
they were before. Persistence is opt-out (persist_traces=False) so the stubbed tests stay
free of both the network and the database.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from agent.loop import ITERATION_LIMIT_MESSAGE, AnthropicLike, run_turn
from config import get_settings
from observability.trace import Trace, save_trace, timer


class Conversation:
    """A stateful multi-turn session over the agent's read tools.

    Holds the message history and grows it one turn at a time. Each ask() runs a full
    tool-calling turn against the accumulated history and returns the assistant's text.
    """

    def __init__(
        self,
        client: AnthropicLike | None = None,
        engine: Engine | None = None,
        persist_traces: bool = True,
    ) -> None:
        self._client = client
        self._engine = engine
        self._persist_traces = persist_traces
        self._messages: list[dict[str, Any]] = []
        self.last_trace_id: str | None = None

    def _trace_engine(self) -> Engine:
        """The engine traces are written to, built from config on first save and reused."""
        if self._engine is None:
            self._engine = create_engine(get_settings().database_url)
        return self._engine

    def _save_trace(self, trace: Trace) -> None:
        """Persist a trace, or give up quietly if persisting fails.

        Tracing observes a turn; it must never break it. A trace lost to a database outage
        costs a record, while raising here would cost the caller an answer the agent has
        already produced, so the failure is swallowed on purpose. The catch is broad for
        the same reason the loop's is: any failure to record must not become a failure to
        answer. Phase 5 adds the structured logging that reports the loss.
        """
        if not self._persist_traces:
            return
        try:
            save_trace(trace, self._trace_engine())
        except Exception:
            return

    @property
    def history(self) -> list[dict[str, Any]]:
        """A snapshot of the message history so far, for inspection and tests.

        Only the outer list is copied; the message dicts are shared with the session, so
        do not modify them.
        """
        return list(self._messages)

    @property
    def turn_count(self) -> int:
        """How many questions have a recorded turn (failed or discarded turns not counted)."""
        return sum(1 for m in self._messages if m["role"] == "user" and _is_question(m))

    def ask(self, question: str) -> str:
        """Ask a question in the context of the running conversation and return the answer.

        The turn runs on a working copy of the history plus this question. Only when the
        model finishes it with a non-empty answer does that copy, with the answer appended,
        become the history, so the next turn sees this exchange. If run_turn raises, the
        exception propagates and the history is unchanged. An empty answer or the iteration
        guard's notice is returned but not kept.

        The turn is traced and, unless persistence is off, the trace is saved whatever
        happened: an empty answer, the guard's notice, or a crash. A turn that failed is the
        one most worth reading back, so the save runs in a finally and the exception still
        propagates. last_trace_id names the trace so a caller can read it back.
        """
        pending = [*self._messages, {"role": "user", "content": question}]
        trace = Trace(question=question)
        self.last_trace_id = trace.trace_id
        try:
            with timer() as turn_timer:
                answer = run_turn(pending, client=self._client, trace=trace)

            if answer and answer != ITERATION_LIMIT_MESSAGE:
                pending.append({"role": "assistant", "content": answer})
                self._messages = pending
            return answer
        finally:
            trace.total_ms = turn_timer.elapsed_ms
            self._save_trace(trace)


def _is_question(message: dict[str, Any]) -> bool:
    """A user turn is a question (not a fed-back tool result) when its content is a string."""
    return isinstance(message.get("content"), str)
