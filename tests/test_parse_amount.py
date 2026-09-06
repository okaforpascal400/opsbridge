# tests/test_parse_amount.py
"""
Fifth normalizer test: parse_amount.

The legacy amount column holds two shapes of the same whole-naira value
(legacy/seed_data.py:255-258): naira_text like "N162,500" (an "N" prefix with comma
thousands separators) and bare_integer like "54550". Amounts are whole naira with no
kobo (AMOUNT_MINIMUM..AMOUNT_MAXIMUM in steps of 50), so stripping to digits is safe
here. The result is a Decimal, never a float, because currency must be represented
exactly. A value with no digits returns None rather than raising.
"""
from __future__ import annotations

from decimal import Decimal

from schema_adapter.reconcile import parse_amount


def test_parses_naira_text_with_prefix_and_commas():
    assert parse_amount("N162,500") == Decimal("162500")


def test_parses_bare_integer_string():
    assert parse_amount("54550") == Decimal("54550")


def test_parses_plain_int_input():
    # the DB may hand back an int rather than a string
    assert parse_amount(54550) == Decimal("54550")


def test_result_is_decimal_not_float():
    result = parse_amount("N162,500")
    assert isinstance(result, Decimal)


def test_strips_incidental_whitespace():
    assert parse_amount("  N162,500  ") == Decimal("162500")


def test_no_digits_returns_none():
    assert parse_amount("no digits here") is None


def test_empty_returns_none():
    assert parse_amount("") is None
