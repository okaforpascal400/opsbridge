# tests/test_build_order.py
"""
Row builder test: build_order.

build_order runs the five normalizers over one raw legacy order row and returns EITHER
a clean CanonicalOrder OR a RejectedRow carrying the raw row and a reason. A field is
required when the record is meaningless or unmatchable without it: name, phone, amount,
and date are required, so any one of them failing to canonicalize quarantines the whole
row (DECISION 012 spirit, extended). status is soft: it always resolves to an
OrderStatus (UNKNOWN if blank/unrecognized, per DECISION 016), so it never causes
rejection. When a row is rejected, the reason names which required field(s) failed, so
the quarantine bucket is actionable rather than an opaque "bad row".
"""
from __future__ import annotations

from decimal import Decimal

from schema_adapter.models import CanonicalOrder, OrderStatus, RejectedRow
from schema_adapter.reconcile import build_order


def _good_row(**overrides) -> dict:
    row = {
        "order_id": 1,
        "cust_name": "Amaka Balogun",
        "customer": None,
        "order_date": "2026-06-15",
        "status": "confirmed",
        "phone": "08031234567",
        "amount": "N162,500",
    }
    row.update(overrides)
    return row


def test_clean_row_builds_canonical_order():
    result = build_order(_good_row())
    assert isinstance(result, CanonicalOrder)
    assert result.order_id == 1
    assert result.customer_name == "Amaka Balogun"
    assert result.phone == "+2348031234567"
    assert result.amount == Decimal("162500")
    assert result.status == OrderStatus.CONFIRMED


def test_uses_customer_column_when_cust_name_blank():
    result = build_order(_good_row(cust_name="", customer="Bola Lawal"))
    assert isinstance(result, CanonicalOrder)
    assert result.customer_name == "Bola Lawal"


def test_blank_status_still_builds_with_unknown():
    # status is soft: an unclear state does not reject the row
    result = build_order(_good_row(status=""))
    assert isinstance(result, CanonicalOrder)
    assert result.status == OrderStatus.UNKNOWN


def test_missing_name_is_rejected():
    result = build_order(_good_row(cust_name="", customer=""))
    assert isinstance(result, RejectedRow)
    assert "name" in result.reason.lower()


def test_unparseable_date_is_rejected():
    result = build_order(_good_row(order_date="not a date"))
    assert isinstance(result, RejectedRow)
    assert "date" in result.reason.lower()


def test_missing_phone_is_rejected():
    result = build_order(_good_row(phone="123"))
    assert isinstance(result, RejectedRow)
    assert "phone" in result.reason.lower()


def test_unparseable_amount_is_rejected():
    result = build_order(_good_row(amount="no digits"))
    assert isinstance(result, RejectedRow)
    assert "amount" in result.reason.lower()


def test_rejected_row_carries_source():
    bad = _good_row(cust_name="", customer="")
    result = build_order(bad)
    assert isinstance(result, RejectedRow)
    assert result.source == bad


def test_multiple_failures_named_in_reason():
    # both phone and amount are unusable; the reason should mention both
    result = build_order(_good_row(phone="123", amount="no digits"))
    assert isinstance(result, RejectedRow)
    assert "phone" in result.reason.lower()
    assert "amount" in result.reason.lower()
