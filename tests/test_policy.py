# tests/test_policy.py
"""
Policy validation tests.

The policy is the safety gate, so these tests exhaustively cover both the allow and the
reject path for each action. They use hand-built orders and matches (the policy is pure,
so no database is needed), which is the point: every rejection is provable without I/O.
The headline safety property is test_refund_on_low_confidence_match_is_rejected, a refund
is refused when the backing match is only LOW confidence.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from guardrails.models import ActionProposal, ActionType
from guardrails.policy import validate_proposal
from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    MatchConfidence,
    OrderStatus,
    ReturnMatch,
)


def _order(order_id: int, status: OrderStatus = OrderStatus.PENDING) -> CanonicalOrder:
    # pending by default: only an open order can be confirmed, so the shared ORDERS
    # fixture has to be confirmable for the non-status tests to mean anything
    return CanonicalOrder(
        order_id=order_id,
        customer_name="Amaka Balogun",
        order_date=date(2026, 6, 15),
        status=status,
        phone="+2348031234567",
        amount=Decimal("162500"),
    )


def _match(return_row: int, order_id: int, confidence: MatchConfidence) -> ReturnMatch:
    return ReturnMatch(
        return_row=return_row,
        matched_order_id=order_id,
        confidence=confidence,
        score=0.95 if confidence == MatchConfidence.HIGH else 0.6,
        rationale="test match",
    )


def _return(return_row: int, amount: str) -> CanonicalReturn:
    return CanonicalReturn(
        return_row=return_row,
        customer_name="Amaka Balogun",
        phone="+2348031234567",
        amount=Decimal(amount),
        reason="damaged",
    )


# every order is worth 162500, so a return above that is more than the customer paid
ORDERS = [_order(1), _order(2)]


# --- rationale gate -----------------------------------------------------------------


@pytest.mark.parametrize("action", list(ActionType))
def test_blank_rationale_is_rejected(action):
    # the model's min_length=1 counts whitespace as content, so the gate is what
    # enforces "requires a reason" for every action
    p = ActionProposal(action=action, order_id=1, rationale="   ")
    result = validate_proposal(p, ORDERS)
    assert result.allowed is False
    assert "requires a rationale" in result.reason


def test_rationale_with_surrounding_whitespace_is_accepted():
    # only blank is rejected; a real reason is not made invalid by stray whitespace
    p = ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=1, rationale="  fraud  ")
    result = validate_proposal(p, ORDERS)
    assert result.allowed is True


# --- order existence gate -----------------------------------------------------------


def test_confirm_missing_order_is_rejected():
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=999, rationale="x")
    result = validate_proposal(p, ORDERS)
    assert result.allowed is False
    assert "does not exist" in result.reason


def test_confirm_existing_order_is_allowed():
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="x")
    result = validate_proposal(p, ORDERS)
    assert result.allowed is True


# --- confirm: only an order that is still open ---------------------------------------


def test_confirm_pending_order_is_allowed():
    orders = [_order(1, OrderStatus.PENDING)]
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="x")
    result = validate_proposal(p, orders)
    assert result.allowed is True


def test_confirm_unknown_status_order_is_allowed():
    # an unreadable source status is not evidence the order moved on, so it stays open
    orders = [_order(1, OrderStatus.UNKNOWN)]
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="x")
    result = validate_proposal(p, orders)
    assert result.allowed is True


def test_confirm_delivered_order_is_rejected():
    orders = [_order(1, OrderStatus.DELIVERED)]
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="x")
    result = validate_proposal(p, orders)
    assert result.allowed is False
    assert "has status delivered" in result.reason


def test_confirm_returned_order_is_rejected():
    orders = [_order(1, OrderStatus.RETURNED)]
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="x")
    result = validate_proposal(p, orders)
    assert result.allowed is False
    assert "has status returned" in result.reason


def test_confirm_already_confirmed_order_is_rejected():
    orders = [_order(1, OrderStatus.CONFIRMED)]
    p = ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=1, rationale="x")
    result = validate_proposal(p, orders)
    assert result.allowed is False
    assert "has status confirmed" in result.reason


@pytest.mark.parametrize("status", [OrderStatus.DELIVERED, OrderStatus.RETURNED])
def test_terminal_status_does_not_block_hold(status):
    # the rule belongs to confirm_order alone; a terminal order can still be held
    orders = [_order(1, status)]
    p = ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=1, rationale="chargeback")
    result = validate_proposal(p, orders)
    assert result.allowed is True


# --- hold ---------------------------------------------------------------------------


def test_hold_existing_order_is_allowed():
    p = ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=2, rationale="suspected fraud")
    result = validate_proposal(p, ORDERS)
    assert result.allowed is True


def test_hold_missing_order_is_rejected():
    p = ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=999, rationale="x")
    result = validate_proposal(p, ORDERS)
    assert result.allowed is False


# --- refund note: the money action, strictest rules ---------------------------------


def test_refund_with_high_confidence_match_is_allowed():
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 3 matched order 1",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is True


def test_refund_without_supporting_return_is_rejected():
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="no backing return",
    )
    result = validate_proposal(p, ORDERS)
    assert result.allowed is False
    assert "requires a supporting return" in result.reason


def test_refund_on_low_confidence_match_is_rejected():
    # the headline safety rule: a refund cannot rest on an uncertain (LOW) match
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.LOW)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 3 loosely matched order 1",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is False
    assert "HIGH-confidence" in result.reason


def test_refund_when_match_points_to_a_different_order_is_rejected():
    # return 3 matched order 2 at HIGH, but the proposal targets order 1
    matches = [_match(return_row=3, order_id=2, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="mismatched target",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is False


def test_refund_when_the_cited_return_row_has_no_match_is_rejected():
    # the mirror of the case above: the HIGH match points at order 1, but it backs
    # return 3, not the row this proposal cites, so it cannot justify this refund
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="cites a return row the matcher never tied to this order",
        supporting_return_row=99,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is False
    assert "HIGH-confidence" in result.reason


def test_refund_finds_its_match_anywhere_in_the_run():
    # a real run emits one match per return, so the backing match is rarely the first
    matches = [
        _match(return_row=0, order_id=2, confidence=MatchConfidence.LOW),
        _match(return_row=7, order_id=2, confidence=MatchConfidence.HIGH),
        _match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH),
    ]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 3 matched order 1",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is True


def test_refund_on_return_row_zero_is_allowed():
    # return rows are 0-based, so row 0 is a real first row and not a sentinel:
    # the missing-return check must stay an "is None" check, not a falsy one
    matches = [_match(return_row=0, order_id=1, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 0 matched order 1",
        supporting_return_row=0,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is True


def test_refund_below_the_order_amount_is_allowed():
    # a partial refund is normal: most seeded returns are worth less than the order
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    returns = [_return(return_row=3, amount="54550")]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="partial refund on return 3",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, returns, matches=matches)
    assert result.allowed is True


def test_refund_equal_to_the_order_amount_is_allowed():
    # a full refund sits exactly on the ceiling, so the check must not be >=
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    returns = [_return(return_row=3, amount="162500")]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="full refund on return 3",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, returns, matches=matches)
    assert result.allowed is True


def test_refund_above_the_order_amount_is_rejected():
    # the money ceiling: a refund cannot pay out more than the order was worth
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    returns = [_return(return_row=3, amount="5000000")]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="return 3 claims more than the order was worth",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, returns, matches=matches)
    assert result.allowed is False
    assert "exceeds" in result.reason


def test_missing_order_is_checked_before_action_specifics():
    # even a refund with a plausible return is rejected if the order itself is gone
    matches = [_match(return_row=3, order_id=999, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=999,
        rationale="order does not exist",
        supporting_return_row=3,
    )
    result = validate_proposal(p, ORDERS, matches=matches)
    assert result.allowed is False
    assert "does not exist" in result.reason


@pytest.mark.parametrize("status", [OrderStatus.DELIVERED, OrderStatus.RETURNED])
def test_terminal_status_does_not_block_refund(status):
    # the status rule is confirm-only by design: a delivered or returned order is exactly
    # the kind you refund, so a terminal status must NOT block a valid refund note
    orders = [_order(1, status)]
    returns = [_return(return_row=3, amount="54550")]
    matches = [_match(return_row=3, order_id=1, confidence=MatchConfidence.HIGH)]
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=1,
        rationale="delivered order, valid return, refund stands",
        supporting_return_row=3,
    )
    result = validate_proposal(p, orders, returns, matches=matches)
    assert result.allowed is True
