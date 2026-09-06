from __future__ import annotations

from datetime import date
from decimal import Decimal

from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    OrderStatus,
    ReconciliationResult,
    RejectedRow,
)


def _clean_optional_text(value: str | None) -> str | None:
    """
    Return stripped text when usable, otherwise None.
    """
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned if cleaned else None


def normalize_name(
    cust_name: str | None,
    customer: str | None,
) -> str | None:
    """
    Reconcile the legacy `cust_name` and `customer` columns.

    `cust_name` has precedence when both columns contain usable values.
    We do not combine conflicting values because reconciliation should
    normalize source data, not invent new data.
    """
    return _clean_optional_text(cust_name) or _clean_optional_text(customer)


def parse_order_date(raw: str) -> date | None:
    raise NotImplementedError


def normalize_status(raw: str | None) -> OrderStatus:
    raise NotImplementedError


def normalize_phone(raw: str | None) -> str | None:
    raise NotImplementedError


def parse_amount(raw: str | int) -> Decimal | None:
    raise NotImplementedError


def build_order(row: dict) -> CanonicalOrder | RejectedRow:
    raise NotImplementedError


def build_return(
    row: dict,
    return_row_index: int,
) -> CanonicalReturn | RejectedRow:
    raise NotImplementedError


def reconcile(
    order_rows: list[dict],
    return_rows: list[dict],
) -> ReconciliationResult:
    raise NotImplementedError