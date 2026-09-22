"""
Demo console tests.

GET / serves api/static/index.html, the single page the demo runs on. These tests need no
database and no model: they check that the page is served, that it is served from the
module's own directory rather than the working directory, and that every route the page
fetches is one the app actually registers, so a renamed route fails here instead of
silently breaking the demo.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

# what the page fetches, mapped to the route path the app registers for it
PAGE_CALLS = {
    "/summary": "/summary",
    "/ask": "/ask",
    "/confirm": "/confirm",
    "/trace/": "/trace/{trace_id}",
}


def test_the_console_is_served_as_html():
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "OpsBridge" in response.text


def test_the_console_is_served_from_any_working_directory(monkeypatch, tmp_path: Path):
    # the path is resolved from the module's location, so a request served while the
    # process sits in some other directory still finds the page
    monkeypatch.chdir(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "OpsBridge" in response.text


def test_every_route_the_page_calls_is_registered():
    body = client.get("/").text
    served = {route.path for route in app.routes}

    for fetched, route_path in PAGE_CALLS.items():
        assert fetched in body, f"the page no longer fetches {fetched}"
        assert route_path in served, f"the page fetches {fetched} but no route serves it"
