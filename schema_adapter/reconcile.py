"""
Reconciles the messy legacy sources into the clean canonical contract.

Each normalizer is a small, pure, individually testable function.
Row builders run the normalizers and either produce a canonical object
or quarantine the row.

The fuzzy matcher for returns to orders is deliberately left for a later
step, after the normalizers are solid and tested.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    OrderStatus,
    ReconciliationResult,
    RejectedRow,
)

_STATUS_MAP: dict[str, OrderStatus] = {
    "confirmed": OrderStatus.CONFIRMED,
    "conf": OrderStatus.CONFIRMED,
    "pending call": OrderStatus.PENDING,
    "pending": OrderStatus.PENDING,
    "delivered": OrderStatus.DELIVERED,
    "returned?": OrderStatus.RETURNED,
}


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
    """
    Parse a legacy order date using the three known source formats.

    Supported formats:
    - ISO:       2026-06-15
    - Slashed:   07/03/2026 -> day/month/year
    - Long form: March 1, 2026

    Returns None when the value is blank or does not match any known format.
    """
    text = _clean_optional_text(raw)

    if text is None:
        return None

    formats = (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%B %d, %Y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def normalize_status(raw: str | None) -> OrderStatus:
    """
    Map a legacy free-text status onto the OrderStatus enum.

    Matching is case-insensitive and whitespace-tolerant. Both blank and
    unrecognized values return UNKNOWN: an unknown status does not make an
    order unusable, so we record it honestly rather than guess or reject.
    """
    cleaned = _clean_optional_text(raw)

    if cleaned is None:
        return OrderStatus.UNKNOWN

    return _STATUS_MAP.get(cleaned.lower(), OrderStatus.UNKNOWN)


def normalize_phone(raw: str | None) -> str | None:
    """
    Normalize a legacy phone into one canonical form: "+234" plus the last ten
    significant digits.

    The three legacy formats all wrap the same ten digits
    (international "+234"+digits, national "0"+digits, bare digits),
    so stripping to digits and taking the last ten collapses every format
    to the same value.

    This lets the fuzzy matcher treat two records for the same real number
    as a match. A value that cannot yield ten digits returns None rather
    than raising.
    """
    text = _clean_optional_text(raw)

    if text is None:
        return None

    digits = "".join(
        character for character in text if character.isdigit()
    )

    if len(digits) < 10:
        return None

    return f"+234{digits[-10:]}"


def parse_amount(raw: str | int) -> Decimal | None:
    """
    Parse a legacy amount into a money-safe Decimal.

    The amount column holds either "N162,500" (N prefix, comma separators)
    or a bare integer like 54550. Amounts are whole naira with no kobo,
    so stripping to digits is safe for this data.

    Returns a Decimal, never a float, or None when there are no digits
    to parse.
    """
    text = str(raw).strip()
    digits = "".join(
        character for character in text if character.isdigit()
    )

    if not digits:
        return None

    return Decimal(digits)


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