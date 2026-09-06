# tests/test_normalize_phone.py
"""
Fourth normalizer test: normalize_phone.

This normalizer matters twice: it cleans the phone AND it is the join key for the fuzzy
matcher, so the SAME real number in any of the three legacy formats must produce the
IDENTICAL canonical string. The three formats all wrap the same ten significant digits
(legacy/seed_data.py: international "+234"+digits, national "0"+digits, bare digits),
and the generator's own _local_number_of canonicalizes by taking the last ten digits.
The canonical form here is full international: "+234" followed by those ten digits.
A value that cannot yield ten digits returns None (never crash, per DECISION 012 spirit).
"""
from __future__ import annotations

from schema_adapter.reconcile import normalize_phone

# The same real number, in each of the three legacy formats, must collapse to one string.
CANONICAL = "+2348031234567"


def test_international_format():
    assert normalize_phone("+2348031234567") == CANONICAL


def test_national_format_with_leading_zero():
    assert normalize_phone("08031234567") == CANONICAL


def test_bare_format():
    assert normalize_phone("8031234567") == CANONICAL


def test_all_three_formats_collapse_to_same_string():
    # the core property the matcher depends on
    a = normalize_phone("+2348031234567")
    b = normalize_phone("08031234567")
    c = normalize_phone("8031234567")
    assert a == b == c == CANONICAL


def test_strips_incidental_whitespace():
    assert normalize_phone("  08031234567  ") == CANONICAL


def test_none_returns_none():
    assert normalize_phone(None) is None


def test_empty_returns_none():
    assert normalize_phone("") is None


def test_too_few_digits_returns_none():
    # fewer than ten significant digits cannot be a valid number -> None, not a crash
    assert normalize_phone("12345") is None
