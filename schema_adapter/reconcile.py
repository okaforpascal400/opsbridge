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
    """
    cleaned = _clean_optional_text(raw)

    if cleaned is None:
        return OrderStatus.UNKNOWN

    return _STATUS_MAP.get(cleaned.lower(), OrderStatus.UNKNOWN)


def normalize_phone(raw: str | None) -> str | None:
    """
    Normalize a legacy phone into one canonical form.
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
    """
    text = str(raw).strip()

    digits = "".join(
        character for character in text if character.isdigit()
    )

    if not digits:
        return None

    return Decimal(digits)


def build_order(row: dict) -> CanonicalOrder | RejectedRow:
    """
    Build a CanonicalOrder from one raw legacy order row, or quarantine it.
    """
    name = normalize_name(
        row.get("cust_name"),
        row.get("customer"),
    )
    order_date = parse_order_date(row.get("order_date"))
    phone = normalize_phone(row.get("phone"))
    amount = parse_amount(row.get("amount"))
    status = normalize_status(row.get("status"))

    failures: list[str] = []

    if name is None:
        failures.append("no usable name")

    if order_date is None:
        failures.append("unparseable date")

    if phone is None:
        failures.append("no usable phone")

    if amount is None:
        failures.append("unparseable amount")

    if failures:
        return RejectedRow(
            source=row,
            reason="; ".join(failures),
        )

    return CanonicalOrder(
        order_id=row["order_id"],
        customer_name=name,
        order_date=order_date,
        status=status,
        phone=phone,
        amount=amount,
    )


def build_return(
    row: dict,
    return_row_index: int,
) -> CanonicalReturn | RejectedRow:
    """
    Build a CanonicalReturn from one raw returns CSV row, or quarantine it.
    """
    name = normalize_name(row.get("customer_name"), None)
    phone = normalize_phone(row.get("phone"))
    amount = parse_amount(row.get("amount"))
    reason = _clean_optional_text(row.get("reason")) or ""

    failures: list[str] = []

    if name is None:
        failures.append("no usable name")

    if phone is None:
        failures.append("no usable phone")

    if amount is None:
        failures.append("unparseable amount")

    if failures:
        return RejectedRow(
            source=row,
            reason="; ".join(failures),
        )

    return CanonicalReturn(
        return_row=return_row_index,
        customer_name=name,
        phone=phone,
        amount=amount,
        reason=reason,
    )


def reconcile(
    order_rows: list[dict],
    return_rows: list[dict],
) -> ReconciliationResult:
    """
    Run every raw row through its builder and collect the outcomes into one result.
    """
    orders: list[CanonicalOrder] = []
    returns: list[CanonicalReturn] = []
    rejected: list[RejectedRow] = []

    for row in order_rows:
        built = build_order(row)

        if isinstance(built, RejectedRow):
            rejected.append(built)
        else:
            orders.append(built)

    for index, row in enumerate(return_rows):
        built = build_return(row, index)

        if isinstance(built, RejectedRow):
            rejected.append(built)
        else:
            returns.append(built)

    return ReconciliationResult(
        orders=orders,
        returns=returns,
        rejected=rejected,
    )