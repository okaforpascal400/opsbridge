# tests/test_normalize_name.py
"""
First normalizer test: normalize_name.

Write schema_adapter.reconcile.normalize_name until every test here passes.
These encode the rules from the docstring plus DECISION 012 (both-empty -> None so the
caller can quarantine). The both-populated rule is locked by
test_both_populated_cust_name_wins per DECISION 014: cust_name wins.
"""
from __future__ import annotations

from schema_adapter.reconcile import normalize_name


def test_uses_cust_name_when_only_cust_name_present():
    assert normalize_name("Amaka Balogun", None) == "Amaka Balogun"


def test_uses_customer_when_only_customer_present():
    assert normalize_name(None, "Bola Lawal") == "Bola Lawal"


def test_uses_customer_when_cust_name_is_empty_string():
    assert normalize_name("", "Nneka Musa") == "Nneka Musa"


def test_treats_whitespace_only_as_empty():
    # a name that is only spaces is not a usable name
    assert normalize_name("   ", "Kelechi Chukwu") == "Kelechi Chukwu"


def test_returns_none_when_both_empty():
    # DECISION 012: no usable name in either column -> None (caller quarantines)
    assert normalize_name("", "") is None


def test_returns_none_when_both_none():
    assert normalize_name(None, None) is None


def test_strips_surrounding_whitespace_from_result():
    assert normalize_name("  Segun Chukwu  ", None) == "Segun Chukwu"


def test_both_populated_cust_name_wins():
    assert normalize_name("Ada Okafor", "Grace Obi") == "Ada Okafor"
