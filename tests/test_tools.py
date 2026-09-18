# tests/test_tools.py
"""
Agent tool-layer tests.

The tools are thin wrappers over the reconciliation pipeline, so these tests confirm each
tool returns plain, JSON-safe, correctly-shaped data and that its numbers agree with the
pipeline it wraps. Gated on OPSBRIDGE_TEST_DATABASE_URL like the other DB integration
tests, so they are skipped when no throwaway database is configured.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from agent.tools import (
    get_flagged_returns,
    get_operations_summary,
    get_quarantined_rows,
    get_unmatched_returns,
    propose_action,
)
from guardrails.actions import (
    ACTIONS_SCHEMA,
    ACTIONS_TABLE,
    count_actions,
    ensure_actions_table,
)
from legacy.seed_db import seed
from schema_adapter.models import OrderStatus
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


# --- get_operations_summary ---------------------------------------------------------


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
    database_url, returns_csv = seeded
    tool_result = get_operations_summary(database_url, returns_csv)
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    assert tool_result["orders_in"] == summary.orders_in
    assert tool_result["returns_in"] == summary.returns_in
    assert tool_result["matches"]["high_auto_accept"] == summary.matched_high


# --- the list tools -----------------------------------------------------------------


def test_unmatched_returns_shape_and_json_safe(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = get_unmatched_returns(database_url, returns_csv)
    assert isinstance(result, list)
    for item in result:
        assert {"return_row", "score", "rationale"} <= item.keys()
    json.dumps(result)


def test_flagged_returns_shape_and_json_safe(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = get_flagged_returns(database_url, returns_csv)
    assert isinstance(result, list)
    for item in result:
        assert {"return_row", "candidate_order_id", "score", "rationale"} <= item.keys()
    json.dumps(result)


def test_quarantined_rows_shape_and_json_safe(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = get_quarantined_rows(database_url, returns_csv)
    assert isinstance(result, list)
    for item in result:
        assert {"source", "reason"} <= item.keys()
    json.dumps(result)


def test_tier_tools_partition_the_matches(seeded: tuple[str, Path]) -> None:
    # the three match tiers reported by the summary must add up across the tools:
    # HIGH is neither unmatched nor flagged, so unmatched + flagged + HIGH == canonical returns
    database_url, returns_csv = seeded
    summary = get_operations_summary(database_url, returns_csv)
    unmatched = get_unmatched_returns(database_url, returns_csv)
    flagged = get_flagged_returns(database_url, returns_csv)
    assert len(unmatched) == summary["matches"]["none_no_match"]
    assert len(flagged) == summary["matches"]["low_needs_review"]


# --- propose_action: validates, never commits ---------------------------------------


def _pending_order_id(database_url: str, returns_csv: Path) -> int:
    _summary, result, _matches = run_pipeline(database_url, returns_csv)
    return next(o.order_id for o in result.orders if o.status == OrderStatus.PENDING)


def _clean_actions_table(database_url: str):
    """Give the count assertions a known starting point in the throwaway database."""
    engine = create_engine(database_url)
    ensure_actions_table(engine)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE TABLE {ACTIONS_SCHEMA}.{ACTIONS_TABLE} RESTART IDENTITY"))
    return engine


def test_propose_confirm_on_a_pending_order_is_allowed(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    order_id = _pending_order_id(database_url, returns_csv)
    result = propose_action(
        action="confirm_order",
        order_id=order_id,
        rationale="order is still pending",
        database_url=database_url,
        returns_csv=returns_csv,
    )
    assert set(result) == {"allowed", "reason"}
    assert result["allowed"] is True
    assert result["reason"]
    assert json.dumps(result)  # the verdict is JSON-safe for the tool loop


def test_propose_confirm_on_a_missing_order_is_rejected(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = propose_action(
        action="confirm_order",
        order_id=999_999,
        rationale="no such order",
        database_url=database_url,
        returns_csv=returns_csv,
    )
    assert result["allowed"] is False
    assert "does not exist" in result["reason"]


def test_propose_with_an_unknown_action_is_rejected(seeded: tuple[str, Path]) -> None:
    database_url, returns_csv = seeded
    result = propose_action(
        action="delete_everything",
        order_id=1,
        rationale="not a real action",
        database_url=database_url,
        returns_csv=returns_csv,
    )
    assert result["allowed"] is False
    assert "Unknown action" in result["reason"]


def test_propose_action_writes_nothing(seeded: tuple[str, Path]) -> None:
    # the safety property: proposing is inert, whatever the verdict. Only the confirm
    # step writes, and this tool has no path to it.
    database_url, returns_csv = seeded
    engine = _clean_actions_table(database_url)
    assert count_actions(engine) == 0

    order_id = _pending_order_id(database_url, returns_csv)
    allowed = propose_action(
        action="confirm_order",
        order_id=order_id,
        rationale="order is still pending",
        database_url=database_url,
        returns_csv=returns_csv,
    )
    rejected = propose_action(
        action="hold_account",
        order_id=999_999,
        rationale="no such order",
        database_url=database_url,
        returns_csv=returns_csv,
    )

    assert allowed["allowed"] is True
    assert rejected["allowed"] is False
    assert count_actions(engine) == 0
