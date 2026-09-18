# tests/test_confirm_endpoint.py
"""
HTTP confirmation endpoint tests.

The /confirm route is the HTTP surface of the guardrail write path. These tests prove the
route enforces the SAME guardrails as the function: a malformed body is rejected at the
boundary (422), a well-formed but policy-refused proposal returns 200 with allowed=false
and writes nothing, and a valid proposal returns allowed=true and writes exactly one audit
row. The write tests go through POST /confirm rather than the helper, so the route's own
wiring (body to proposal, verdict to response) is covered too.

They run against a real throwaway Postgres (gated on OPSBRIDGE_TEST_DATABASE_URL), which
the fixture seeds itself: nothing earlier in the suite seeds, so the file cannot rely on
another test having done it. The route reads the configured database, so the fixture
overrides the get_database_url dependency to point it at the throwaway one.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.main import ConfirmResponse, _confirm_proposal, app, get_database_url
from guardrails.actions import (
    ACTIONS_SCHEMA,
    ACTIONS_TABLE,
    count_actions,
    ensure_actions_table,
)
from guardrails.models import ActionProposal, ActionType
from legacy.seed_db import seed
from observability.trace import Trace
from schema_adapter.models import MatchConfidence, OrderStatus
from schema_adapter.pipeline import run_pipeline

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"

client = TestClient(app)


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping endpoint DB test.")
    return url


@pytest.fixture
def seeded_api(tmp_path: Path):
    """Seed the throwaway database, empty the audit table, and point the route at it."""
    url = _require_test_database_url()
    seed(database_url=url, returns_csv=tmp_path / "returns.csv")
    eng = create_engine(url)
    ensure_actions_table(eng)
    # empty between tests for deterministic counts, but never drop: the audit table is
    # the thing the write path promises only ever grows
    with eng.begin() as conn:
        conn.execute(
            text(f"TRUNCATE TABLE {ACTIONS_SCHEMA}.{ACTIONS_TABLE} RESTART IDENTITY")
        )
    app.dependency_overrides[get_database_url] = lambda: url
    try:
        yield url, eng
    finally:
        app.dependency_overrides.clear()
        eng.dispose()


# --- boundary validation (no DB needed) ---------------------------------------------


def test_malformed_body_is_rejected_with_422():
    # missing rationale, an unknown action, and a non-positive order id are all boundary
    # errors FastAPI rejects before any guardrail runs
    r = client.post("/confirm", json={"action": "confirm_order", "order_id": 1})
    assert r.status_code == 422


def test_unknown_action_is_rejected_with_422():
    r = client.post(
        "/confirm",
        json={"action": "delete_everything", "order_id": 1, "rationale": "x"},
    )
    assert r.status_code == 422


def test_an_unknown_field_is_rejected_with_422():
    # the server would not act on it, so it is refused rather than silently dropped
    r = client.post(
        "/confirm",
        json={
            "action": "confirm_order",
            "order_id": 1,
            "rationale": "x",
            "confirmed_by": "amaka",
        },
    )
    assert r.status_code == 422
    assert "confirmed_by" in r.text


def test_an_oversized_rationale_is_rejected_with_422():
    # the rationale lands in an append-only table, so it is capped at the boundary
    r = client.post(
        "/confirm",
        json={"action": "confirm_order", "order_id": 1, "rationale": "x" * 2001},
    )
    assert r.status_code == 422


def test_health_still_works():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- the real write path, over HTTP, against the seeded DB ---------------------------


def _pending_order_id(url: str) -> int:
    # a confirmable order: status pending, so the policy allows a confirm
    _summary, result, _matches = run_pipeline(url, None)
    for order in result.orders:
        if order.status == OrderStatus.PENDING:
            return order.order_id
    raise AssertionError("no pending order in the seed")


def _high_confidence_match(url: str) -> tuple[int, int]:
    """Return (return_row, order_id) for a return the matcher tied to an order at HIGH.

    Deliberately skips row 0 and any row whose number equals its order id, so a route that
    mixed the two fields up could not pass by coincidence.
    """
    _summary, _result, matches = run_pipeline(url, None)
    for match in matches:
        if (
            match.confidence == MatchConfidence.HIGH
            and match.matched_order_id is not None
            and match.return_row > 0
            and match.return_row != match.matched_order_id
        ):
            return match.return_row, match.matched_order_id
    raise AssertionError("no distinguishable high-confidence match in the seed")


def test_valid_confirm_writes_one_row(seeded_api):
    url, eng = seeded_api
    order_id = _pending_order_id(url)

    response = client.post(
        "/confirm",
        json={
            "action": "confirm_order",
            "order_id": order_id,
            "rationale": "pending order, confirming via HTTP",
        },
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True
    assert count_actions(eng) == 1


def test_refused_confirm_writes_nothing(seeded_api):
    url, eng = seeded_api
    # order 999999 does not exist, so the policy refuses: a refusal is a 200 with
    # allowed=false, not a server error, and the table stays empty
    response = client.post(
        "/confirm",
        json={
            "action": "confirm_order",
            "order_id": 999_999,
            "rationale": "ghost order",
        },
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert "does not exist" in response.json()["reason"]
    assert count_actions(eng) == 0


def test_a_refund_keeps_its_supporting_return_through_the_route(seeded_api):
    # the route must carry every field into the proposal: a refund note without its
    # supporting return would be refused, and the audit row must name the return
    url, eng = seeded_api
    return_row, order_id = _high_confidence_match(url)

    response = client.post(
        "/confirm",
        json={
            "action": "issue_refund_note",
            "order_id": order_id,
            "rationale": "return matched this order at high confidence",
            "supporting_return_row": return_row,
        },
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True
    assert count_actions(eng) == 1
    with eng.begin() as conn:
        row = conn.execute(
            text(
                f"SELECT action, order_id, supporting_return_row "
                f"FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}"
            )
        ).one()
    assert row.action == "issue_refund_note"
    assert row.order_id == order_id
    assert row.supporting_return_row == return_row


def test_the_helper_matches_the_route(seeded_api):
    # the route is a thin wrapper: the helper it calls returns the same verdict shape
    url, _eng = seeded_api
    proposal = ActionProposal(
        action=ActionType.CONFIRM_ORDER,
        order_id=_pending_order_id(url),
        rationale="pending order",
    )
    result = _confirm_proposal(proposal, database_url=url)
    assert isinstance(result, ConfirmResponse)
    assert result.allowed is True


def test_a_posted_trace_id_is_recorded_on_the_audit_row(seeded_api):
    # a confirmation that names the turn it came from can be traced back to it
    url, eng = seeded_api
    order_id = _pending_order_id(url)
    trace_id = Trace().trace_id  # a real uuid hex, not a short literal a truncation survives

    response = client.post(
        "/confirm",
        json={
            "action": "confirm_order",
            "order_id": order_id,
            "rationale": "confirming what the agent proposed",
            "trace_id": trace_id,
        },
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is True
    with eng.begin() as conn:
        row = conn.execute(
            text(f"SELECT trace_id FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}")
        ).one()
    assert row.trace_id == trace_id


def test_a_confirmation_without_a_trace_id_stores_null(seeded_api):
    url, eng = seeded_api
    order_id = _pending_order_id(url)

    client.post(
        "/confirm",
        json={
            "action": "confirm_order",
            "order_id": order_id,
            "rationale": "confirmed by hand",
        },
    )

    with eng.begin() as conn:
        row = conn.execute(
            text(f"SELECT trace_id FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}")
        ).one()
    assert row.trace_id is None


def test_an_oversized_trace_id_is_rejected_with_422():
    # the id lands in an append-only table, so it is capped like the rationale is
    r = client.post(
        "/confirm",
        json={
            "action": "confirm_order",
            "order_id": 1,
            "rationale": "x",
            "trace_id": "t" * 65,
        },
    )
    assert r.status_code == 422
