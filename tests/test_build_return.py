# tests/test_build_return.py
"""
Row builder test: build_return.

build_return runs the normalizers over one raw returns CSV row and returns EITHER a clean
CanonicalReturn OR a RejectedRow. Returns have no order_id, so build_return takes the
source row index to identify the return. The CSV columns are customer_name, phone,
amount, reason. Required fields are name, phone, and amount (returns have no date); any
one failing quarantines the row with a reason naming the failed field(s). reason is soft
per DECISION 013: a blank reason is stored as "" and never causes rejection.
"""
from __future__ import annotations

from decimal import Decimal

from schema_adapter.models import CanonicalReturn, RejectedRow
from schema_adapter.reconcile import build_return


def _good_return(**overrides) -> dict:
    row = {
        "customer_name": "Amaka Balogun",
        "phone": "08031234567",
        "amount": "N96,850",
        "reason": "wrong size",
    }
    row.update(overrides)
    return row


def test_clean_row_builds_canonical_return():
    result = build_return(_good_return(), 0)
    assert isinstance(result, CanonicalReturn)
    assert result.return_row == 0
    assert result.customer_name == "Amaka Balogun"
    assert result.phone == "+2348031234567"
    assert result.amount == Decimal("96850")
    assert result.reason == "wrong size"


def test_blank_reason_is_accepted_as_empty_string():
    # DECISION 013: a missing reason is valid, stored as ""
    result = build_return(_good_return(reason=""), 5)
    assert isinstance(result, CanonicalReturn)
    assert result.reason == ""


def test_none_reason_is_accepted_as_empty_string():
    result = build_return(_good_return(reason=None), 5)
    assert isinstance(result, CanonicalReturn)
    assert result.reason == ""


def test_return_row_index_is_carried():
    result = build_return(_good_return(), 37)
    assert isinstance(result, CanonicalReturn)
    assert result.return_row == 37


def test_missing_name_is_rejected():
    result = build_return(_good_return(customer_name=""), 2)
    assert isinstance(result, RejectedRow)
    assert "name" in result.reason.lower()


def test_missing_phone_is_rejected():
    result = build_return(_good_return(phone="123"), 2)
    assert isinstance(result, RejectedRow)
    assert "phone" in result.reason.lower()


def test_unparseable_amount_is_rejected():
    result = build_return(_good_return(amount="no digits"), 2)
    assert isinstance(result, RejectedRow)
    assert "amount" in result.reason.lower()


def test_rejected_row_carries_source():
    bad = _good_return(customer_name="")
    result = build_return(bad, 9)
    assert isinstance(result, RejectedRow)
    assert result.source == bad
