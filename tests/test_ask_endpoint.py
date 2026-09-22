"""
Ask endpoint tests, using a stubbed Conversation.

POST /ask calls the real model, so these tests never let it: every test replaces the
Conversation the route builds with a stub that records the question and returns a canned
answer, and replaces create_engine with a fake that records its url and whether it was
disposed. No test here reaches the network, the Anthropic API, or a database. What they
cover is the route's own wiring (question in, answer and trace id out), the lifetime of
the engine the trace is saved through, and the boundary validation, which must refuse a
bad body before any agent is built.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app, get_database_url

STUB_ANSWER = "Two returns need review."
STUB_TRACE_ID = "0123456789abcdef0123456789abcdef"
ROUTE_DATABASE_URL = "postgresql+psycopg2://stub:stub@stub-host:5432/stub_db"

client = TestClient(app)


class _FakeEngine:
    """Stands in for a SQLAlchemy engine: remembers its url and whether it was disposed."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _StubConversation:
    """Stands in for agent.conversation.Conversation: same ask() and last_trace_id."""

    trace_id: str | None = STUB_TRACE_ID
    error: Exception | None = None
    instances: list[_StubConversation] = []

    def __init__(self, engine: _FakeEngine | None = None) -> None:
        self.engine = engine
        self.last_trace_id: str | None = None
        self.questions: list[str] = []
        _StubConversation.instances.append(self)

    def ask(self, question: str) -> str:
        self.questions.append(question)
        self.last_trace_id = self.trace_id
        if self.error is not None:
            raise self.error
        return STUB_ANSWER


@pytest.fixture(autouse=True)
def stub_conversation(monkeypatch):
    """Stub the route's Conversation and engine for every test, so none can leave the process."""
    _StubConversation.instances = []
    _StubConversation.trace_id = STUB_TRACE_ID
    _StubConversation.error = None
    monkeypatch.setattr(api_main, "Conversation", _StubConversation)
    monkeypatch.setattr(api_main, "create_engine", _FakeEngine)
    app.dependency_overrides[get_database_url] = lambda: ROUTE_DATABASE_URL
    try:
        yield _StubConversation
    finally:
        app.dependency_overrides.clear()


def test_ask_returns_the_answer_and_trace_id(stub_conversation):
    response = client.post("/ask", json={"question": "what needs review?"})

    assert response.status_code == 200
    assert response.json() == {"answer": STUB_ANSWER, "trace_id": STUB_TRACE_ID}
    assert len(stub_conversation.instances) == 1
    assert stub_conversation.instances[0].questions == ["what needs review?"]


def test_a_missing_trace_id_is_served_as_an_empty_string(stub_conversation):
    stub_conversation.trace_id = None

    response = client.post("/ask", json={"question": "what needs review?"})

    assert response.status_code == 200
    assert response.json() == {"answer": STUB_ANSWER, "trace_id": ""}


def test_the_trace_engine_uses_the_route_database_and_is_disposed(stub_conversation):
    # left to Conversation, each request would build an engine nobody disposes and leak
    # its pooled connection, so the route builds it from its dependency and disposes it
    client.post("/ask", json={"question": "what needs review?"})

    engine = stub_conversation.instances[0].engine
    assert isinstance(engine, _FakeEngine)
    assert engine.url == ROUTE_DATABASE_URL
    assert engine.disposed is True


def test_the_trace_engine_is_disposed_when_the_turn_raises(stub_conversation):
    # a turn that raises is still traced, and must not leak the engine it was traced with
    stub_conversation.error = RuntimeError("model unreachable")

    with pytest.raises(RuntimeError, match="model unreachable"):
        client.post("/ask", json={"question": "what needs review?"})

    assert stub_conversation.instances[0].engine.disposed is True


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"question": ""}, id="empty"),
        pytest.param({"question": "x" * 1001}, id="oversized"),
        pytest.param({}, id="missing"),
        pytest.param({"question": "status?", "session_id": "abc"}, id="unknown-field"),
    ],
)
def test_a_bad_body_is_rejected_with_422_before_the_agent_runs(stub_conversation, body):
    response = client.post("/ask", json=body)

    assert response.status_code == 422
    assert stub_conversation.instances == []


def test_a_question_at_the_cap_is_accepted(stub_conversation):
    # pins the cap at exactly 1000, so a tightened limit shows up as a failure here
    response = client.post("/ask", json={"question": "x" * 1000})

    assert response.status_code == 200
    assert stub_conversation.instances[0].questions == ["x" * 1000]
