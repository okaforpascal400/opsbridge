# observability/trace.py
"""
Observability for the agent loop: a durable record of what each turn did and why.

A Trace accumulates TraceSteps as a turn runs, one per model call and one per tool call,
each with its inputs, outputs, latency, and any error. At the end of a turn the trace is
persisted to Postgres so it survives restarts and can be read back later; observability
that is lost on restart is not observability, and Phase 6 evals read past traces to score
runs. Traces live in the same opsbridge schema as the audit actions, so "what the system
did and why" is one coherent, queryable story.

Tracing only observes: it records what the loop does and never changes the loop's result.
The tables are created idempotently and never dropped, mirroring the actions audit table.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

try:  # the same "lost DDL race is success" set the actions table uses
    from psycopg2 import errors as pg_errors

    _RACE_LOST_ERRORS: Final = (
        pg_errors.UniqueViolation,
        pg_errors.DuplicateSchema,
        pg_errors.DuplicateTable,
        pg_errors.DuplicateObject,
    )
except ImportError:  # psycopg2 is always present in this project; guard just in case
    _RACE_LOST_ERRORS = ()

TRACE_SCHEMA: Final[str] = "opsbridge"
TRACES_TABLE: Final[str] = "traces"
TRACE_STEPS_TABLE: Final[str] = "trace_steps"

_CREATE_SCHEMA_SQL: Final[str] = f"CREATE SCHEMA IF NOT EXISTS {TRACE_SCHEMA}"
_CREATE_TRACES_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TRACE_SCHEMA}.{TRACES_TABLE} (
    trace_id      TEXT PRIMARY KEY,
    question      TEXT NOT NULL,
    final_answer  TEXT NOT NULL,
    total_ms      INTEGER NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_CREATE_STEPS_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TRACE_SCHEMA}.{TRACE_STEPS_TABLE} (
    step_id     SERIAL PRIMARY KEY,
    trace_id    TEXT NOT NULL REFERENCES {TRACE_SCHEMA}.{TRACES_TABLE}(trace_id),
    step_index  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    input_json  TEXT NOT NULL,
    output_json TEXT NOT NULL,
    latency_ms  INTEGER NOT NULL,
    error       TEXT NULL
)
"""
_INSERT_TRACE_SQL: Final[str] = f"""
INSERT INTO {TRACE_SCHEMA}.{TRACES_TABLE} (trace_id, question, final_answer, total_ms)
VALUES (:trace_id, :question, :final_answer, :total_ms)
"""
_INSERT_STEP_SQL: Final[str] = f"""
INSERT INTO {TRACE_SCHEMA}.{TRACE_STEPS_TABLE}
    (trace_id, step_index, kind, name, input_json, output_json, latency_ms, error)
VALUES
    (:trace_id, :step_index, :kind, :name, :input_json, :output_json, :latency_ms, :error)
"""


class TraceStep(BaseModel):
    """One recorded step in a turn: a model call or a tool call."""

    model_config = ConfigDict(extra="forbid")

    kind: str  # "model_call" or "tool_call"
    name: str
    input: Any = None
    output: Any = None
    latency_ms: int = Field(ge=0)
    error: str | None = None


class Trace(BaseModel):
    """The full record of one agent turn. Accumulates steps, then is persisted."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    question: str = ""
    final_answer: str = ""
    total_ms: int = 0
    steps: list[TraceStep] = Field(default_factory=list)

    def add_step(self, step: TraceStep) -> None:
        self.steps.append(step)


class _Timer:
    """Context manager that measures elapsed milliseconds."""

    def __enter__(self) -> _Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)


def timer() -> _Timer:
    """Return a context manager exposing .elapsed_ms after the block."""
    return _Timer()


def _execute_ignoring_lost_race(engine: Engine, statement: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(statement))
    except DBAPIError as exc:
        if not isinstance(exc.orig, _RACE_LOST_ERRORS):
            raise


def ensure_trace_tables(engine: Engine) -> None:
    """Create the opsbridge schema and the trace tables if absent. Non-destructive."""
    _execute_ignoring_lost_race(engine, _CREATE_SCHEMA_SQL)
    _execute_ignoring_lost_race(engine, _CREATE_TRACES_SQL)
    _execute_ignoring_lost_race(engine, _CREATE_STEPS_SQL)


def save_trace(trace: Trace, engine: Engine) -> None:
    """Persist a completed trace and all its steps in one transaction."""
    ensure_trace_tables(engine)
    with engine.begin() as conn:
        conn.execute(
            text(_INSERT_TRACE_SQL),
            {
                "trace_id": trace.trace_id,
                "question": trace.question,
                "final_answer": trace.final_answer,
                "total_ms": trace.total_ms,
            },
        )
        for index, step in enumerate(trace.steps):
            conn.execute(
                text(_INSERT_STEP_SQL),
                {
                    "trace_id": trace.trace_id,
                    "step_index": index,
                    "kind": step.kind,
                    "name": step.name,
                    "input_json": json.dumps(step.input, default=str),
                    "output_json": json.dumps(step.output, default=str),
                    "latency_ms": step.latency_ms,
                    "error": step.error,
                },
            )


def load_trace(trace_id: str, engine: Engine) -> dict | None:
    """Read a trace and its steps back as a plain dict, or None if it does not exist."""
    ensure_trace_tables(engine)
    with engine.connect() as conn:
        head = conn.execute(
            text(
                f"SELECT trace_id, question, final_answer, total_ms, created_at "
                f"FROM {TRACE_SCHEMA}.{TRACES_TABLE} WHERE trace_id = :tid"
            ),
            {"tid": trace_id},
        ).mappings().first()
        if head is None:
            return None
        steps = conn.execute(
            text(
                f"SELECT step_index, kind, name, input_json, output_json, latency_ms, error "
                f"FROM {TRACE_SCHEMA}.{TRACE_STEPS_TABLE} "
                f"WHERE trace_id = :tid ORDER BY step_index"
            ),
            {"tid": trace_id},
        ).mappings().all()
    return {
        "trace_id": head["trace_id"],
        "question": head["question"],
        "final_answer": head["final_answer"],
        "total_ms": head["total_ms"],
        "created_at": str(head["created_at"]),
        "steps": [
            {
                "kind": s["kind"],
                "name": s["name"],
                "input": json.loads(s["input_json"]),
                "output": json.loads(s["output_json"]),
                "latency_ms": s["latency_ms"],
                "error": s["error"],
            }
            for s in steps
        ],
    }
