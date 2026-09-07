# tests/test_pipeline.py
"""
Integration test: the full pipeline against the real seeded sources.

Unlike the unit tests that feed hand-built dicts, this runs run_pipeline against the
actual seeded Postgres orders and the committed returns CSV, then asserts the aggregate
numbers are sane. It is gated on OPSBRIDGE_TEST_DATABASE_URL exactly like the Phase 1
DB tests, so it is skipped when no throwaway database is configured.

These assertions are ranges and invariants, not exact magic numbers, so tuning the
matcher thresholds does not force a rewrite. What they pin: every source row is
accounted for (canonicalized plus quarantined equals the input), and the seeded data
that is mostly clean produces mostly-canonical output with real matches.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from legacy.seed_db import seed
from schema_adapter.pipeline import run_pipeline

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"
ORDER_COUNT = 200
RETURN_COUNT = 40


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping DB integration test.")
    return url


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[str, Path]:
    """Seed a throwaway database and a temp returns CSV, return both handles."""
    database_url = _require_test_database_url()
    returns_csv = tmp_path / "returns.csv"
    seed(database_url=database_url, returns_csv=returns_csv)
    return database_url, returns_csv


def test_every_order_row_is_accounted_for(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    assert summary.orders_in == ORDER_COUNT
    assert summary.orders_canonicalized + summary.orders_quarantined == ORDER_COUNT


def test_every_return_row_is_accounted_for(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    assert summary.returns_in == RETURN_COUNT
    assert summary.returns_canonicalized + summary.returns_quarantined == RETURN_COUNT


def test_most_orders_canonicalize(seeded: tuple[str, Path]) -> None:
    # the seed is mostly clean, so the large majority should canonicalize
    database_url, returns_csv = seeded
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    assert summary.orders_canonicalized >= ORDER_COUNT * 0.8


def test_match_counts_cover_every_canonical_return(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    summary, _result, matches = run_pipeline(database_url, returns_csv)
    total_matches = summary.matched_high + summary.matched_low + summary.matched_none
    assert total_matches == summary.returns_canonicalized
    assert len(matches) == summary.returns_canonicalized


def test_some_returns_match_at_high_confidence(seeded: tuple[str, Path]) -> None:
    # the generator pairs most returns to a real order with a reformatted phone, so
    # after normalization a meaningful number should match at HIGH confidence
    database_url, returns_csv = seeded
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    assert summary.matched_high >= 1


def test_high_matches_carry_an_order_id_and_rationale(seeded: tuple[str, Path]) -> None:
    from schema_adapter.models import MatchConfidence

    database_url, returns_csv = seeded
    _summary, _result, matches = run_pipeline(database_url, returns_csv)
    high = [m for m in matches if m.confidence == MatchConfidence.HIGH]
    for match in high:
        assert match.matched_order_id is not None
        assert len(match.rationale) > 0
