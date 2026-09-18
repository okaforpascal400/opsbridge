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
"""
from __future__ import annotations

from typing import Any

from agent.loop import ITERATION_LIMIT_MESSAGE, AnthropicLike, run_turn


class Conversation:
    """A stateful multi-turn session over the agent's read tools.

    Holds the message history and grows it one turn at a time. Each ask() runs a full
    tool-calling turn against the accumulated history and returns the assistant's text.
    """

    def __init__(self, client: AnthropicLike | None = None) -> None:
        self._client = client
        self._messages: list[dict[str, Any]] = []

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
        """
        pending = [*self._messages, {"role": "user", "content": question}]
        answer = run_turn(pending, client=self._client)
        if answer and answer != ITERATION_LIMIT_MESSAGE:
            pending.append({"role": "assistant", "content": answer})
            self._messages = pending
        return answer


def _is_question(message: dict[str, Any]) -> bool:
    """A user turn is a question (not a fed-back tool result) when its content is a string."""
    return isinstance(message.get("content"), str)
