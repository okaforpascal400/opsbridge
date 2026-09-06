# tests/test_normalize_status.py
"""
Third normalizer test: normalize_status.

Maps the seven free-text status variants the legacy system emits (confirmed, CONF,
pending call, PENDING, delivered, returned?, and empty) onto the OrderStatus enum.
Matching is case-insensitive and whitespace-tolerant. Both an empty value and any
unrecognized value map to UNKNOWN (per DECISION 016): an unknown status does not make
an order unusable, so we record it honestly rather than quarantine the row or guess.
"""
from __future__ import annotations

from schema_adapter.models import OrderStatus
from schema_adapter.reconcile import normalize_status


def test_confirmed_lowercase():
    assert normalize_status("confirmed") == OrderStatus.CONFIRMED


def test_confirmed_abbreviation_uppercase():
    assert normalize_status("CONF") == OrderStatus.CONFIRMED


def test_pending_call_phrase():
    assert normalize_status("pending call") == OrderStatus.PENDING


def test_pending_uppercase():
    assert normalize_status("PENDING") == OrderStatus.PENDING


def test_delivered():
    assert normalize_status("delivered") == OrderStatus.DELIVERED


def test_returned_with_question_mark():
    assert normalize_status("returned?") == OrderStatus.RETURNED


def test_empty_string_is_unknown():
    assert normalize_status("") == OrderStatus.UNKNOWN


def test_none_is_unknown():
    assert normalize_status(None) == OrderStatus.UNKNOWN


def test_whitespace_only_is_unknown():
    assert normalize_status("   ") == OrderStatus.UNKNOWN


def test_case_insensitive_and_whitespace_tolerant():
    # a messy but recognizable variant: mixed case with padding
    assert normalize_status("  Confirmed  ") == OrderStatus.CONFIRMED


def test_unrecognized_value_is_unknown():
    # DECISION 016: an unseen status is honestly UNKNOWN, not a guess and not a crash
    assert normalize_status("cancelled") == OrderStatus.UNKNOWN
