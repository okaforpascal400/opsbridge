# tests/test_match_score.py
"""
Matcher step 1: the scoring helper, _score_candidate.

Given one CanonicalReturn and one candidate CanonicalOrder, produce a 0.0-1.0 confidence
that they are the same customer. The design (DECISION 021, to be refined into its own
decision): phone is the strong identity signal because normalize_phone makes exact
comparison reliable, and names are deliberately noisy, so name similarity confirms rather
than decides. Phone contributes the dominant weight; name similarity (rapidfuzz
token_sort_ratio, scaled 0.0-1.0) contributes a smaller share.

These tests pin the RELATIONSHIPS the scoring must satisfy, not exact magic numbers, so
you can tune the weights without rewriting the tests. The one hard anchor: phone+name
agreement scores higher than either signal alone, and total disagreement scores low.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from schema_adapter.models import CanonicalOrder, CanonicalReturn, OrderStatus
from schema_adapter.reconcile import _score_candidate


def _order(phone: str, name: str, order_id: int = 1) -> CanonicalOrder:
    return CanonicalOrder(
        order_id=order_id,
        customer_name=name,
        order_date=date(2026, 6, 15),
        status=OrderStatus.CONFIRMED,
        phone=phone,
        amount=Decimal("162500"),
    )


def _return(phone: str, name: str) -> CanonicalReturn:
    return CanonicalReturn(
        return_row=0,
        customer_name=name,
        phone=phone,
        amount=Decimal("96850"),
        reason="wrong size",
    )


def test_score_is_between_zero_and_one():
    score = _score_candidate(
        _return("+2348031234567", "Amaka Balogun"),
        _order("+2348031234567", "Amaka Balogun"),
    )
    assert 0.0 <= score <= 1.0


def test_perfect_match_scores_high():
    # same phone, same name -> near the top of the range
    score = _score_candidate(
        _return("+2348031234567", "Amaka Balogun"),
        _order("+2348031234567", "Amaka Balogun"),
    )
    assert score >= 0.9


def test_total_mismatch_scores_low():
    # different phone, different name -> near the bottom
    score = _score_candidate(
        _return("+2348031234567", "Amaka Balogun"),
        _order("+2349990001111", "Chidi Okonkwo"),
    )
    assert score <= 0.3


def test_phone_match_outweighs_name_mismatch():
    # phone matches, name is quite different: should still score moderately high,
    # because phone is the strong signal (this is the flag-for-review zone, not a reject)
    same_phone_diff_name = _score_candidate(
        _return("+2348031234567", "Amaka Balogun"),
        _order("+2348031234567", "Chidi Okonkwo"),
    )
    # name matches, phone is different: should score LOWER than the phone-match case,
    # because a matching name alone is weaker evidence than a matching phone
    diff_phone_same_name = _score_candidate(
        _return("+2348031234567", "Amaka Balogun"),
        _order("+2349990001111", "Amaka Balogun"),
    )
    assert same_phone_diff_name > diff_phone_same_name


def test_name_confirms_when_phone_matches():
    # both have the matching phone; the one with the closer name should score higher
    phone = "+2348031234567"
    close_name = _score_candidate(
        _return(phone, "Amaka Balogun"),
        _order(phone, "Amaka Balogun"),
    )
    far_name = _score_candidate(
        _return(phone, "Amaka Balogun"),
        _order(phone, "Chidi Okonkwo"),
    )
    assert close_name > far_name


def test_minor_name_misspelling_still_scores_high_with_phone_match():
    # the realistic case: same phone, name misspelled slightly (dropped middle, typo)
    score = _score_candidate(
        _return("+2348031234567", "Amaka Balogun"),
        _order("+2348031234567", "Amaka Balogn"),  # missing an 'u'
    )
    assert score >= 0.85
