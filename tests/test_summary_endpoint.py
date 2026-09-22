"""
Summary endpoint tests.

GET /summary is the demo UI's overview: the pipeline's aggregate numbers served over HTTP.
The route test runs against a real throwaway Postgres (gated on OPSBRIDGE_TEST_DATABASE_URL)
that the fixture seeds itself, and overrides get_database_url to point the route at it. The
seed writes its returns CSV to a temp path; the route reads the committed data/returns.csv,
which is the seed's default output, so both hold the same 40 returns.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import SummaryResponse, app, get_database_url
from legacy.seed_db import seed
from schema_adapter.pipeline import PipelineSummary

TEST_DATABASE_URL_ENV = "OPSBRIDGE_TEST_DATABASE_URL"

client = TestClient(app)


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set; skipping summary endpoint test.")
    return url


@pytest.fixture
def seeded_api(tmp_path: Path):
    """Seed the throwaway database and point the route at it."""
    url = _require_test_database_url()
    seed(database_url=url, returns_csv=tmp_path / "returns.csv")
    app.dependency_overrides[get_database_url] = lambda: url
    try:
        yield url
    finally:
        app.dependency_overrides.clear()


def test_the_response_model_carries_every_summary_field():
    # the route builds the response from the pipeline summary's fields, so a field added
    # only to the pipeline would be dropped silently, and one added only to the response
    # would 500 on every request; this catches either without a database
    assert set(SummaryResponse.model_fields) == set(PipelineSummary.model_fields)


def test_summary_reports_the_seeded_numbers(seeded_api):
    response = client.get("/summary")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == set(SummaryResponse.model_fields)
    assert body["orders_in"] == 200
    assert body["returns_in"] == 40
    assert body["matched_high"] == 40
