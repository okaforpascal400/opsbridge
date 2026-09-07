# tests/test_reconcile.py
"""
Entrypoint test: reconcile.

reconcile runs every raw order row through build_order and every raw return row through
build_return, then collects the outcomes into one ReconciliationResult: clean orders,
clean returns, and rejected rows pooled from both sources. Returns are indexed by their
position in the input list. The judgment lives in the builders; reconcile only routes
each outcome into the right bucket.
"""
from __future__ import annotations

from schema_adapter.models import CanonicalOrder, CanonicalReturn, ReconciliationResult, RejectedRow
from schema_adapter.reconcile import reconcile


def _order(**overrides) -> dict:
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


def _return(**overrides) -> dict:
    row = {
        "customer_name": "Amaka Balogun",
        "phone": "08031234567",
        "amount": "N96,850",
        "reason": "wrong size",
    }
    row.update(overrides)
    return row


def test_returns_a_reconciliation_result():
    result = reconcile([], [])
    assert isinstance(result, ReconciliationResult)
    assert result.orders == []
    assert result.returns == []
    assert result.rejected == []


def test_clean_rows_land_in_the_right_buckets():
    result = reconcile([_order(order_id=1), _order(order_id=2)], [_return()])
    assert len(result.orders) == 2
    assert len(result.returns) == 1
    assert result.rejected == []
    assert all(isinstance(o, CanonicalOrder) for o in result.orders)
    assert all(isinstance(r, CanonicalReturn) for r in result.returns)


def test_bad_order_goes_to_rejected():
    good = _order(order_id=1)
    bad = _order(order_id=2, cust_name="", customer="")
    result = reconcile([good, bad], [])
    assert len(result.orders) == 1
    assert len(result.rejected) == 1
    assert isinstance(result.rejected[0], RejectedRow)


def test_bad_return_goes_to_rejected():
    result = reconcile([], [_return(), _return(customer_name="")])
    assert len(result.returns) == 1
    assert len(result.rejected) == 1


def test_rejects_from_both_sources_are_pooled():
    bad_order = _order(order_id=1, cust_name="", customer="")
    bad_return = _return(phone="123")
    result = reconcile([bad_order], [bad_return])
    assert len(result.rejected) == 2


def test_returns_are_indexed_by_position():
    # the second return (index 1) is the clean one; its return_row should be 1
    result = reconcile([], [_return(customer_name=""), _return()])
    assert len(result.returns) == 1
    assert result.returns[0].return_row == 1
