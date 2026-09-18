# tests/test_actions.py
"""
Confirm-flow tests: the write path and its safety property.

The headline property Phase 4 must guarantee: NO action is recorded without passing
confirmation, and confirm re-validates rather than trusting propose. These tests run
against a real throwaway Postgres (gated on OPSBRIDGE_TEST_DATABASE_URL) because the whole
point is the database write. They prove:
  - a rejected proposal writes nothing (the table stays empty)
  - an allowed proposal writes exactly one row
  - propose() never writes, whatever the verdict
  - confirm re-validates at confirm time, so a proposal that would be rejected is rejected
    even if a caller tried to confirm it directly
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text

from guardrails.actions import (
    ACTIONS_SCHEMA,
    ACTIONS_TABLE,
    confirm,
    count_actions,
    ensure_actions_table,
    propose,
)
from guardrails.models import ActionProposal, ActionType
from observability.trace import Trace
from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    MatchConfidence,
    OrderStatus,
    ReturnMatch,
)

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping DB confirm-flow test.")
    return url


@pytest.fixture
def engine():
    url = _require_test_database_url()
    eng = create_engine(url)
    ensure_actions_table(eng)
    # empty the table between tests so counts are deterministic, but never drop it: the
    # audit table is the thing this module promises only ever grows
    with eng.begin() as conn:
        conn.execute(
            text(f"TRUNCATE TABLE {ACTIONS_SCHEMA}.{ACTIONS_TABLE} RESTART IDENTITY")
        )
    try:
        yield eng
    finally:
        eng.dispose()


def _order(order_id: int, status: OrderStatus = OrderStatus.PENDING) -> CanonicalOrder:
    return CanonicalOrder(
        order_id=order_id,
        customer_name="Amaka Balogun",
        order_date=date(2026, 6, 15),
        status=status,
        phone="+2348031234567",
        amount=Decimal("162500"),
    )


def _return(return_row: int, amount: str = "54550") -> CanonicalReturn:
    return CanonicalReturn(
        return_row=return_row,
        customer_name="Amaka Balogun",
        phone="+2348031234567",
        amount=Decimal(amount),
        reason="damaged",
    )


def _match(return_row: int, order_id: int, confidence: MatchConfidence) -> ReturnMatch:
    return ReturnMatch(
        return_row=return_row,
        matched_order_id=order_id,
        confidence=confidence,
        score=0.95 if confidence == MatchConfidence.HIGH else 0.6,
        rationale="test match",
    )


ORDERS = [_order(1)]


def test_table_starts_empty(engine):
    assert count_actions(engine) == 0


def test_confirming_a_valid_proposal_writes_one_row(engine):
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="pending order")
    result = confirm(p, engine, ORDERS)
    assert result.allowed is True
    assert count_actions(engine) == 1


def test_confirming_a_rejected_proposal_writes_nothing(engine):
    # order 999 does not exist, so the policy rejects and nothing is written
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=999, rationale="ghost")
    result = confirm(p, engine, ORDERS)
    assert result.allowed is False
    assert count_actions(engine) == 0


def test_confirm_revalidates_a_bad_refund_and_writes_nothing(engine):
    # a refund on a LOW-confidence match must be refused AT CONFIRM TIME, table untouched
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.LOW)]
    returns = [_return(3)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="shaky match",
        supporting_return_row=3,
    )
    result = confirm(p, engine, ORDERS, returns, matches=matches)
    assert result.allowed is False
    assert count_actions(engine) == 0


def test_confirm_revalidates_against_state_that_drifted_since_propose(engine):
    # the point of re-validating next to the write: propose saw a pending order and
    # allowed it, but by confirm time the order is delivered, so confirm refuses
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="pending then")
    assert propose(p, [_order(1, OrderStatus.PENDING)]).allowed is True

    result = confirm(p, engine, [_order(1, OrderStatus.DELIVERED)])
    assert result.allowed is False
    assert "has status delivered" in result.reason
    assert count_actions(engine) == 0


def test_confirming_a_refund_without_its_return_writes_nothing(engine):
    # the amount ceiling cannot run when the cited return is not supplied, so confirm
    # refuses rather than committing a refund whose amount was never checked
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 3 matched order 1",
        supporting_return_row=3,
    )
    result = confirm(p, engine, ORDERS, matches=matches)
    assert result.allowed is False
    assert "was not supplied to the confirm step" in result.reason
    assert count_actions(engine) == 0


def test_propose_never_writes_even_when_allowed(engine):
    # propose is validation only; it must not touch the table regardless of verdict
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="pending order")
    result = propose(p, ORDERS)
    assert result.allowed is True
    assert count_actions(engine) == 0


def test_a_valid_refund_writes_a_row_with_its_return(engine):
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    returns = [_return(3, amount="54550")]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 3 matched order 1 at high confidence",
        supporting_return_row=3,
    )
    result = confirm(p, engine, ORDERS, returns, matches=matches)
    assert result.allowed is True
    assert count_actions(engine) == 1

    # the recorded row carries the supporting return, not a null
    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"SELECT action, order_id, supporting_return_row, rationale "
                f"FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}"
            )
        ).one()
    assert row.action == "issue_refund_note"
    assert row.order_id == 1
    assert row.supporting_return_row == 3


def test_multiple_confirmations_accumulate(engine):
    # the table is append-only: two valid confirmations leave two rows
    p1 = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="one")
    p2 = ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=1, rationale="two")
    confirm(p1, engine, ORDERS)
    confirm(p2, engine, ORDERS)
    assert count_actions(engine) == 2


def test_a_confirmation_records_the_trace_it_came_from(engine):
    # the correlation: an action proposed by an agent turn names that turn's trace. The id
    # is a real 32-character uuid hex from the producer, so a truncating write would show
    trace_id = Trace().trace_id
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="pending order")
    result = confirm(p, engine, ORDERS, trace_id=trace_id)

    assert result.allowed is True
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT trace_id FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}")
        ).one()
    assert row.trace_id == trace_id


def test_a_confirmation_without_a_trace_stores_null(engine):
    # a human confirming directly has no agent turn behind it, and the row says so
    p = ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=1, rationale="chargeback")
    result = confirm(p, engine, ORDERS)

    assert result.allowed is True
    with engine.begin() as conn:
        row = conn.execute(
            text(f"SELECT trace_id FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}")
        ).one()
    assert row.trace_id is None


def test_an_older_table_repairs_itself(engine):
    # CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a database seeded
    # before trace_id existed would never gain the column and every insert would fail
    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER TABLE {ACTIONS_SCHEMA}.{ACTIONS_TABLE} DROP COLUMN trace_id")
        )
        conn.execute(
            text(
                f"INSERT INTO {ACTIONS_SCHEMA}.{ACTIONS_TABLE} "
                "(action, order_id, rationale) VALUES ('confirm_order', 1, 'from before')"
            )
        )

    ensure_actions_table(engine)
    ensure_actions_table(engine)  # idempotent: running it again is a no-op

    with engine.begin() as conn:
        columns = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE "
                    f"table_schema = '{ACTIONS_SCHEMA}' AND table_name = '{ACTIONS_TABLE}'"
                )
            )
        ]
    assert "trace_id" in columns

    # the row written before the repair survives, with no trace, and writes work again
    trace_id = Trace().trace_id
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="after repair")
    assert confirm(p, engine, ORDERS, trace_id=trace_id).allowed is True
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                f"SELECT rationale, trace_id FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE} "
                "ORDER BY action_id"
            )
        ).all()
    assert [(r.rationale, r.trace_id) for r in rows] == [
        ("from before", None),
        ("after repair", trace_id),
    ]
