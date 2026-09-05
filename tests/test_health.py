"""Tests for the OpsBridge API health endpoint."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_health_reports_ok(client: TestClient) -> None:
    """Deploy checks read this endpoint, so the exact body is part of the contract."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_carries_the_service_title() -> None:
    """The title is what shows up in the generated docs and in Railway logs."""
    assert app.title == "OpsBridge API"


def test_health_declares_a_typed_response_model() -> None:
    """A declared response_model keeps the OpenAPI schema honest as routes are added."""
    schema = app.openapi()
    response = schema["paths"]["/health"]["get"]["responses"]["200"]
    reference = response["content"]["application/json"]["schema"]["$ref"]
    assert reference.endswith("/HealthResponse")
