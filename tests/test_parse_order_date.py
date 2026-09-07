# tests/test_parse_order_date.py
"""
Second normalizer test: parse_order_date.

Parses the three date formats the legacy system mixes into a single date object.
The slashed format is DD/MM/YYYY (day first), confirmed by reading legacy/seed_data.py
line 207: f"{value.day:02d}/{value.month:02d}/{value.year}". Parsing it month-first
would silently corrupt every ambiguous date, so the day-first rule is locked here.
"""
from __future__ import annotations

from datetime import date

from schema_adapter.reconcile import parse_order_date


def test_parses_iso_format():
    assert parse_order_date("2026-06-15") == date(2026, 6, 15)


def test_parses_long_format():
    assert parse_order_date("March 1, 2026") == date(2026, 3, 1)


def test_parses_slashed_as_day_first():
    # 07/03/2026 is the 7th of March, NOT July 3rd (DD/MM/YYYY per the generator)
    assert parse_order_date("07/03/2026") == date(2026, 3, 7)


def test_slashed_unambiguous_day_still_day_first():
    # 25/12/2026: 25 can only be a day, so this pins the ordering beyond doubt
    assert parse_order_date("25/12/2026") == date(2026, 12, 25)


def test_returns_none_for_unparseable():
    assert parse_order_date("not a date") is None


def test_returns_none_for_empty():
    assert parse_order_date("") is None


def test_handles_surrounding_whitespace():
    assert parse_order_date("  2026-06-15  ") == date(2026, 6, 15)
