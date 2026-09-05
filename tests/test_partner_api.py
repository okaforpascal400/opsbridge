"""Tests for the fake partner delivery API.

The partner is deliberately undocumented: XML instead of JSON, its own private
phone key, delivery states that do not line up with the orders table, and a 200
with state UNKNOWN where an unknown number should arguably be a 404. These tests
pin that behaviour down so Phase 3 can be written against something stable.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Final
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from legacy.partner_api import DeliveryRecord, app, normalise_phone, render_delivery_xml
from legacy.seed_data import build_order_rows

NON_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"\D")
ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")

UNKNOWN_PHONE: Final[str] = "0000000000"
STATE_SAMPLE_SIZE: Final[int] = 25


def _last_ten_digits(value: str) -> str:
    """Test-local phone key, kept separate from the partner's own normaliser."""
    return NON_DIGITS_RE.sub("", value)[-10:]


def _three_formats(local_number: str) -> tuple[str, str, str]:
    return f"+234{local_number}", f"0{local_number}", local_number


def _delivery_element(client: TestClient, phone: str) -> ElementTree.Element:
    response = client.get("/status", params={"phone": phone})
    assert response.status_code == 200
    return ElementTree.fromstring(response.text)


def _child_text(root: ElementTree.Element, tag: str) -> str:
    child = root.find(tag)
    assert child is not None, f"missing <{tag}> element"
    return child.text or ""


KNOWN_LOCAL_NUMBER: Final[str] = _last_ten_digits(build_order_rows()[0].phone)


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_ping_returns_plain_ok(client: TestClient) -> None:
    """The liveness check is a bare text body, not JSON, which callers must not assume."""
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.text == "ok"


def test_status_returns_xml_with_the_documented_elements(client: TestClient) -> None:
    """The partner speaks XML, so the integration has to parse rather than read JSON."""
    response = client.get("/status", params={"phone": KNOWN_LOCAL_NUMBER})
    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    root = ElementTree.fromstring(response.text)
    assert root.tag == "delivery"
    assert {child.tag for child in root} == {"phone", "state", "last_update"}


def test_one_delivery_is_reachable_through_all_three_phone_formats(client: TestClient) -> None:
    """The partner keys on digits alone, so all three written forms hit one record.

    The UNKNOWN check stops this passing trivially by having every lookup miss.
    """
    seen = {
        (_child_text(root, "state"), _child_text(root, "last_update"))
        for root in (
            _delivery_element(client, phone) for phone in _three_formats(KNOWN_LOCAL_NUMBER)
        )
    }
    assert len(seen) == 1
    assert next(iter(seen))[0] != "UNKNOWN"


def test_unknown_phone_returns_ok_with_an_unknown_state(client: TestClient) -> None:
    """A miss is a 200 with state UNKNOWN, not a 404. Phase 3 has to handle that."""
    root = _delivery_element(client, UNKNOWN_PHONE)
    assert _child_text(root, "state") == "UNKNOWN"
    assert _child_text(root, "last_update") == ""


def test_last_update_is_a_fixed_date_string(client: TestClient) -> None:
    """Dates come from the seeded fixture world, never from the clock."""
    root = _delivery_element(client, KNOWN_LOCAL_NUMBER)
    assert ISO_DATE_RE.match(_child_text(root, "last_update"))


def test_delivery_states_do_not_reuse_the_order_status_words(client: TestClient) -> None:
    """The two vocabularies disagree on purpose: reconciling them is Phase 2 work."""
    orders = build_order_rows()
    states = {
        _child_text(_delivery_element(client, order.phone), "state")
        for order in orders[:STATE_SAMPLE_SIZE]
    }
    statuses = {order.status for order in orders}
    assert states
    assert not states & statuses


def test_phone_normaliser_collapses_every_written_form() -> None:
    """One number written four ways must produce one key, digits only."""
    assert normalise_phone("+2348031234567") == "8031234567"
    assert normalise_phone("08031234567") == "8031234567"
    assert normalise_phone("8031234567") == "8031234567"
    assert normalise_phone("+234 803 123 4567") == "8031234567"


def test_xml_renderer_emits_a_parseable_delivery_document() -> None:
    """Rendering is unit testable on its own, and it escapes values properly."""
    record = DeliveryRecord(phone="8031234567", state="AT_HUB", last_update="2026-04-01")
    root = ElementTree.fromstring(render_delivery_xml(record))
    assert root.tag == "delivery"
    assert _child_text(root, "phone") == "8031234567"
    assert _child_text(root, "state") == "AT_HUB"
    assert _child_text(root, "last_update") == "2026-04-01"
