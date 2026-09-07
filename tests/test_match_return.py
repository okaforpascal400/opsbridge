# tests/test_match_return.py
"""
Matcher step 2: match_return_to_orders.

Given one CanonicalReturn and the list of candidate CanonicalOrders, score every
candidate with _score_candidate, take the best, and turn its score into a ReturnMatch
with a HIGH / LOW / NONE verdict and a human-readable rationale.

Two thresholds draw the lines:
  score >= HIGH threshold -> HIGH  (auto-accept: both signals agree)
  score >= LOW threshold  -> LOW   (flag for human review: partial evidence)
  below LOW threshold      -> NONE  (no plausible match; matched_order_id is None)

The philosophy (same as DECISION 005's human-in-the-loop): when evidence is partial the
matcher does not guess, it flags LOW for a human. A silent wrong match is worse than an
honest "not sure". The rationale explains WHY, so a reviewer can act on a LOW without
re-deriving it.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    MatchConfidence,
    OrderStatus,
    ReturnMatch,
)
from schema_adapter.reconcile import match_return_to_orders


def _order(phone: str, name: str, order_id: int) -> CanonicalOrder:
    return CanonicalOrder(
        order_id=order_id,
        customer_name=name,
        order_date=date(2026, 6, 15),
        status=OrderStatus.CONFIRMED,
        phone=phone,
        amount=Decimal("162500"),
    )


def _return(phone: str, name: str, row: int = 0) -> CanonicalReturn:
    return CanonicalReturn(
        return_row=row,
        customer_name=name,
        phone=phone,
        amount=Decimal("96850"),
        reason="wrong size",
    )


def test_returns_a_return_match():
    orders = [_order("+2348031234567", "Amaka Balogun", 1)]
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), orders)
    assert isinstance(result, ReturnMatch)


def test_strong_match_is_high_and_picks_the_order():
    orders = [
        _order("+2349990001111", "Chidi Okonkwo", 1),
        _order("+2348031234567", "Amaka Balogun", 2),
    ]
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), orders)
    assert result.confidence == MatchConfidence.HIGH
    assert result.matched_order_id == 2


def test_high_match_survives_minor_misspelling():
    orders = [_order("+2348031234567", "Amaka Balogn", 7)]  # missing a letter
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), orders)
    assert result.confidence == MatchConfidence.HIGH
    assert result.matched_order_id == 7


def test_phone_match_name_mismatch_is_low():
    # phone matches but the name is clearly different: partial evidence -> flag, not accept
    orders = [_order("+2348031234567", "Chidi Okonkwo", 3)]
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), orders)
    assert result.confidence == MatchConfidence.LOW
    assert result.matched_order_id == 3


def test_no_plausible_match_is_none():
    orders = [_order("+2349990001111", "Chidi Okonkwo", 4)]
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), orders)
    assert result.confidence == MatchConfidence.NONE
    assert result.matched_order_id is None


def test_empty_order_list_is_none():
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), [])
    assert result.confidence == MatchConfidence.NONE
    assert result.matched_order_id is None


def test_carries_return_row_and_score_and_rationale():
    orders = [_order("+2348031234567", "Amaka Balogun", 1)]
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun", row=12), orders)
    assert result.return_row == 12
    assert 0.0 <= result.score <= 1.0
    assert len(result.rationale) > 0


def test_picks_highest_scoring_among_several():
    # two orders share the phone; the one with the closer name should win
    orders = [
        _order("+2348031234567", "Chidi Okonkwo", 1),
        _order("+2348031234567", "Amaka Balogun", 2),
    ]
    result = match_return_to_orders(_return("+2348031234567", "Amaka Balogun"), orders)
    assert result.matched_order_id == 2
