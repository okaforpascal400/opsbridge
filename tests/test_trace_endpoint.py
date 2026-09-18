# tests/test_trace_endpoint.py
"""
Trace endpoint tests.

GET /trace/{trace_id} is the debugging surface: it reads back what a turn did, in order.
These tests save a trace through the same save_trace the agent uses, then read it over
HTTP, so the stored shape and the served shape are checked against each other rather than
against a fixture. Gated on OPSBRIDGE_TEST_DATABASE_URL; the route reads the configured
database, so the fixture overrides get_database_url to point it at the throwaway one.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.main import app, get_database_url
from observability.trace import (
    TRACE_SCHEMA,
    TRACE_STEPS_TABLE,
    TRACES_TABLE,
    Trace,
    TraceStep,
    ensure_trace_tables,
    save_trace,
)

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"

client = TestClient(app)


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping trace endpoint test.")
    return url


@pytest.fixture
def traced_api():
    """Empty the trace tables, point the route at the throwaway database."""
    url = _require_test_database_url()
    eng = create_engine(url)
    ensure_trace_tables(eng)
    with eng.begin() as conn:
        conn.execute(
            text(
                f"TRUNCATE TABLE {TRACE_SCHEMA}.{TRACE_STEPS_TABLE}, "
                f"{TRACE_SCHEMA}.{TRACES_TABLE} RESTART IDENTITY"
            )
        )
    app.dependency_overrides[get_database_url] = lambda: url
    try:
        yield eng
    finally:
        app.dependency_overrides.clear()
        eng.dispose()


def _saved_trace(engine) -> Trace:
    trace = Trace(
        question="what needs review?",
        final_answer="One return needs review.",
        total_ms=42,
    )
    trace.add_step(
        TraceStep(
            kind="model_call",
            name="claude-sonnet-5",
            input={"messages": 1},
            output={"stop_reason": "tool_use"},
            latency_ms=12,
        )
    )
    trace.add_step(
        TraceStep(
            kind="tool_call",
            name="get_flagged_returns",
            input={},
            output={"flagged": 1},
            latency_ms=8,
        )
    )
    trace.add_step(
        TraceStep(
            kind="model_call",
            name="claude-sonnet-5",
            input={"messages": 3},
            output={"stop_reason": "end_turn"},
            latency_ms=15,
        )
    )
    save_trace(trace, engine)
    return trace


def test_a_saved_trace_reads_back_over_http(traced_api):
    engine = traced_api
    trace = _saved_trace(engine)

    response = client.get(f"/trace/{trace.trace_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == trace.trace_id
    assert body["question"] == "what needs review?"
    assert body["final_answer"] == "One return needs review."
    assert body["total_ms"] == 42
    assert body["created_at"]
    assert [step["kind"] for step in body["steps"]] == [
        "model_call",
        "tool_call",
        "model_call",
    ]
    assert [step["name"] for step in body["steps"]] == [
        "claude-sonnet-5",
        "get_flagged_returns",
        "claude-sonnet-5",
    ]
    assert [step["latency_ms"] for step in body["steps"]] == [12, 8, 15]


def test_a_step_error_is_served(traced_api):
    engine = traced_api
    trace = Trace(question="how many orders?", final_answer="", total_ms=9)
    trace.add_step(
        TraceStep(
            kind="tool_call",
            name="get_operations_summary",
            input={},
            output=None,
            latency_ms=4,
            error="database unavailable",
        )
    )
    save_trace(trace, engine)

    body = client.get(f"/trace/{trace.trace_id}").json()

    assert body["final_answer"] == ""
    assert body["steps"][0]["error"] == "database unavailable"
    assert body["steps"][0]["output"] is None


def test_an_unknown_trace_id_is_404(traced_api):
    response = client.get("/trace/does-not-exist")

    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]
