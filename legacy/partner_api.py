"""Simulated partner delivery API: the undocumented third party in the stack.

This is a second FastAPI app, deliberately separate from the OpsBridge API. It
stands in for an external delivery service that OpsBridge has to integrate with:
XML responses, a phone-number convention nobody wrote down, and a cheerful 200 for
numbers it has never seen. It holds every record in memory, so it needs no
database and no network.

Delivery records are derived from the same seeded order rows the database is built
from, so the fixture world stays reproducible across the whole repo.

Run it with:
    uvicorn legacy.partner_api:app --port 8100
"""

from __future__ import annotations

import random
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Final

from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict

from legacy.seed_data import RANDOM_SEED, LegacyOrderRow, build_order_rows

# The partner's own delivery vocabulary. It does not line up with the free-text
# status values in legacy.orders, and that mismatch is the point: two systems
# describing the same order with different words is the normal case.
DELIVERY_STATES: Final[tuple[str, ...]] = (
    "IN_TRANSIT",
    "DELIVERED",
    "AT_HUB",
    "FAILED_ATTEMPT",
    "RETURNED",
)

UNKNOWN_STATE: Final[str] = "UNKNOWN"
PHONE_KEY_DIGITS: Final[int] = 10
UPDATE_WINDOW_START: Final[date] = date(2026, 1, 5)
UPDATE_WINDOW_DAYS: Final[int] = 180

_NON_DIGIT: Final[re.Pattern[str]] = re.compile(r"[^0-9]")


class DeliveryRecord(BaseModel):
    """One delivery as the partner sees it, before it is rendered to XML."""

    model_config = ConfigDict(frozen=True)

    phone: str
    state: str
    last_update: str


def normalise_phone(raw: str) -> str:
    """Reduce a phone number to the partner's lookup key: its last 10 digits.

    This is the partner's private convention, not a published contract. OpsBridge
    needs its own normaliser in Phase 2 and must not import this one: a partner can
    change internal behaviour without telling anyone, and our reconciliation rules
    have to be ours to test and defend.
    """
    digits = _NON_DIGIT.sub("", raw)
    return digits[-PHONE_KEY_DIGITS:]


def render_delivery_xml(record: DeliveryRecord) -> str:
    """Render a delivery record as an XML document body.

    ElementTree does the escaping, so a name or state containing "&" or "<" cannot
    produce a broken document the way an f-string template would.
    """
    root = ET.Element("delivery")
    ET.SubElement(root, "phone").text = record.phone
    ET.SubElement(root, "state").text = record.state
    ET.SubElement(root, "last_update").text = record.last_update
    return ET.tostring(root, encoding="unicode")


def _build_delivery_index(rows: Sequence[LegacyOrderRow]) -> dict[str, DeliveryRecord]:
    """Index one delivery record per normalised phone, deterministically."""
    rng = random.Random(RANDOM_SEED)
    index: dict[str, DeliveryRecord] = {}
    for row in rows:
        state = rng.choice(DELIVERY_STATES)
        last_update = UPDATE_WINDOW_START + timedelta(days=rng.randrange(UPDATE_WINDOW_DAYS))
        key = normalise_phone(row.phone)
        index[key] = DeliveryRecord(phone=key, state=state, last_update=last_update.isoformat())
    return index


DELIVERY_INDEX: Final[dict[str, DeliveryRecord]] = _build_delivery_index(build_order_rows())


def lookup_delivery(phone: str) -> DeliveryRecord:
    """Return the record for a phone number, or an UNKNOWN placeholder.

    An unknown number is not an error here: the partner answers 200 with state
    UNKNOWN and a blank last_update. That is the kind of undocumented behaviour
    that bites an integrator, because a naive client reads "not a 404" as "found"
    and happily stores a delivery state that means nothing. Phase 3 has to detect
    and handle it.
    """
    key = normalise_phone(phone)
    known = DELIVERY_INDEX.get(key)
    if known is not None:
        return known
    return DeliveryRecord(phone=key, state=UNKNOWN_STATE, last_update="")


app: FastAPI = FastAPI(title="Partner Delivery API")


@app.get("/ping", response_class=PlainTextResponse)
def ping() -> PlainTextResponse:
    """Liveness check. Plain text, because that is what the partner sends."""
    return PlainTextResponse("ok", media_type="text/plain")


@app.get("/status")
def delivery_status(phone: str) -> Response:
    """Return the delivery state for a phone number as an XML document."""
    body = render_delivery_xml(lookup_delivery(phone))
    return Response(content=body, media_type="application/xml")
