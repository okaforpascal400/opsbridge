# tests/test_tools.py
"""
Agent tool-layer tests.

The tools are thin wrappers over the reconciliation pipeline, so these tests confirm the
tool returns plain, correctly-shaped data (what a tool-calling API needs) and that its
numbers agree with the pipeline it wraps. Gated on OPSBRIDGE_TEST_DATABASE_URL like the
other DB integration tests, so it is skipped when no throwaway database is configured.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.tools import get_operations_summary
from legacy.seed_db import seed
from schema_adapter.pipeline import run_pipeline

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping DB tool test.")
    return url


@pytest.fixture
def seeded(tmp_path: Path) -> tuple[str, Path]:
    database_url = _require_test_database_url()
    returns_csv = tmp_path / "returns.csv"
    seed(database_url=database_url, returns_csv=returns_csv)
    return database_url, returns_csv


def test_summary_is_a_plain_dict(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = get_operations_summary(database_url, returns_csv)
    assert isinstance(result, dict)


def test_summary_has_the_expected_shape(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = get_operations_summary(database_url, returns_csv)
    for key in (
        "orders_in",
        "orders_canonicalized",
        "orders_quarantined",
        "returns_in",
        "returns_canonicalized",
        "returns_quarantined",
        "matches",
    ):
        assert key in result
    for tier in ("high_auto_accept", "low_needs_review", "none_no_match"):
        assert tier in result["matches"]


def test_summary_numbers_agree_with_the_pipeline(seeded: tuple[str, Path]) -> None:
    # the tool must not compute anything of its own; its numbers are the pipeline's
    database_url, returns_csv = seeded
    tool_result = get_operations_summary(database_url, returns_csv)
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    assert tool_result["orders_in"] == summary.orders_in
    assert tool_result["returns_in"] == summary.returns_in
    assert tool_result["matches"]["high_auto_accept"] == summary.matched_high


def test_summary_values_are_json_safe(seeded: tuple[str, Path]) -> None:
    # a tool result is handed to a tool-calling API, so it must serialize to JSON
    import json

    database_url, returns_csv = seeded
    result = get_operations_summary(database_url, returns_csv)
    json.dumps(result)  # raises if any value is not JSON-serializable
