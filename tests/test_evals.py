# tests/test_evals.py
"""
Tests for the eval harness itself.

The eval runner is the thing that measures the system, so it needs its own tests: that
every golden case runs, that the pass/fail scoring is correct, and that the report renders.
The DB-backed cases are gated on OPSBRIDGE_TEST_DATABASE_URL; the policy and matcher cases
need no database. The headline assertion is test_all_golden_cases_pass: on the committed
seed, every expected value holds, so this suite doubles as a regression guard.
"""
from __future__ import annotations

import os

import pytest

from evals.golden import GOLDEN_CASES, EvalCategory
from evals.run import format_report, run_evals

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping DB-backed evals.")
    return url


def test_golden_dataset_has_cases_across_categories():
    cats = {c.category for c in GOLDEN_CASES}
    assert EvalCategory.PIPELINE in cats
    assert EvalCategory.POLICY in cats
    assert EvalCategory.MATCHER in cats
    assert len(GOLDEN_CASES) >= 10


def test_case_ids_are_unique():
    ids = [c.id for c in GOLDEN_CASES]
    assert len(ids) == len(set(ids))


def test_policy_and_matcher_cases_pass_without_a_database():
    # the non-pipeline cases need no DB; run just those and assert all pass
    from evals.run import _run_case

    non_db = [c for c in GOLDEN_CASES if c.check not in ("summary_field", "summary_match_tier")]
    results = [_run_case(c) for c in non_db]
    failed = [r.case.id for r in results if not r.passed]
    assert failed == [], f"policy/matcher cases failed: {failed}"


def test_report_renders_pass_count():
    from evals.run import _run_case

    non_db = [c for c in GOLDEN_CASES if c.check not in ("summary_field", "summary_match_tier")]
    results = [_run_case(c) for c in non_db]
    report = format_report(results)
    assert "passed" in report
    assert "| id |" in report


def test_all_golden_cases_pass(seeded_db):
    # the full suite against the real seed: every expected value must hold
    url = seeded_db
    results = run_evals(url)
    failed = [(r.case.id, r.expected, r.actual) for r in results if not r.passed]
    assert failed == [], f"golden cases failed: {failed}"


@pytest.fixture
def seeded_db() -> str:
    from legacy.seed_db import seed

    url = _require_test_database_url()
    seed(database_url=url)
    return url
