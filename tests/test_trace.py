# tests/test_trace.py
"""
Trace store tests.

The trace model accumulates steps in memory; the store persists a completed trace to
Postgres and reads it back. The DB tests are gated on OPSBRIDGE_TEST_DATABASE_URL like the
other database tests. They prove a round trip: what is saved is what is loaded, steps come
back in order, an absent trace loads as None, and the timer measures elapsed time. The
in-memory model tests need no database.
"""
from __future__ import annotations

import os
import time

import pytest
from sqlalchemy import create_engine, text

from observability.trace import (
    TRACE_SCHEMA,
    TRACE_STEPS_TABLE,
    TRACES_TABLE,
    Trace,
    TraceStep,
    ensure_trace_tables,
    load_trace,
    save_trace,
    timer,
)

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping trace DB test.")
    return url


@pytest.fixture
def engine():
    url = _require_test_database_url()
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {TRACE_SCHEMA}.{TRACE_STEPS_TABLE}"))
        conn.execute(text(f"DROP TABLE IF EXISTS {TRACE_SCHEMA}.{TRACES_TABLE}"))
    ensure_trace_tables(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# --- in-memory model (no DB) --------------------------------------------------------


def test_trace_gets_a_unique_id_by_default():
    a = Trace()
    b = Trace()
    assert a.trace_id != b.trace_id
    assert len(a.trace_id) > 0


def test_add_step_accumulates():
    t = Trace(question="q")
    t.add_step(TraceStep(kind="model_call", name="claude", latency_ms=10))
    t.add_step(TraceStep(kind="tool_call", name="get_operations_summary", latency_ms=5))
    assert len(t.steps) == 2
    assert t.steps[1].name == "get_operations_summary"


def test_timer_measures_elapsed():
    with timer() as t:
        time.sleep(0.02)
    assert t.elapsed_ms >= 15  # at least ~20ms slept, allow scheduling slack


# --- persistence round trip (DB) ----------------------------------------------------


def test_absent_trace_loads_as_none(engine):
    assert load_trace("does-not-exist", engine) is None


def test_save_then_load_round_trips(engine):
    trace = Trace(question="how many returns?", final_answer="40 returns.", total_ms=123)
    trace.add_step(
        TraceStep(
            kind="model_call",
            name="claude",
            input={"messages": 1},
            output={"stop_reason": "tool_use"},
            latency_ms=80,
        )
    )
    trace.add_step(
        TraceStep(
            kind="tool_call",
            name="get_operations_summary",
            input={},
            output={"orders_in": 200},
            latency_ms=40,
        )
    )
    save_trace(trace, engine)

    loaded = load_trace(trace.trace_id, engine)
    assert loaded is not None
    assert loaded["question"] == "how many returns?"
    assert loaded["final_answer"] == "40 returns."
    assert loaded["total_ms"] == 123
    assert len(loaded["steps"]) == 2
    # steps come back in order
    assert loaded["steps"][0]["kind"] == "model_call"
    assert loaded["steps"][1]["name"] == "get_operations_summary"
    assert loaded["steps"][1]["output"] == {"orders_in": 200}


def test_step_error_is_preserved(engine):
    trace = Trace(question="q", final_answer="a", total_ms=1)
    trace.add_step(
        TraceStep(
            kind="tool_call",
            name="broken_tool",
            latency_ms=2,
            error="boom",
        )
    )
    save_trace(trace, engine)
    loaded = load_trace(trace.trace_id, engine)
    assert loaded["steps"][0]["error"] == "boom"
