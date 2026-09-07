"""
Reconciles the messy legacy sources into the clean canonical contract.

Each normalizer is a small, pure, individually testable function.
Row builders run the normalizers and either produce a canonical object
or quarantine the row.

The fuzzy matcher for returns to orders is deliberately built on top of
canonicalized names and phones so the matching logic works with cleaned,
comparable identity signals.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from rapidfuzz import fuzz

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


# Weights for combining the two identity signals. Phone dominates because
# normalize_phone makes exact phone comparison reliable, while names are
# deliberately noisy in the data, so name similarity confirms rather than decides.
_PHONE_WEIGHT: float = 0.7
_NAME_WEIGHT: float = 0.3


def _score_candidate(
    ret: CanonicalReturn,
    order: CanonicalOrder,
) -> float:
    """
    Score how likely a return and a candidate order are the same customer,
    in the range 0.0-1.0.

    Phone is the strong signal: an exact match of the canonical phones
    contributes the dominant weight.

    Name similarity uses rapidfuzz token_sort_ratio, scaled from 0-100
    down to 0.0-1.0. It contributes a smaller share and confirms or
    weakens the match.

    The weighting means a phone match with a somewhat different name can
    still score higher than a strong name match with a different phone.
    """
    phone_signal = 1.0 if ret.phone == order.phone else 0.0

    name_signal = (
        fuzz.token_sort_ratio(
            ret.customer_name,
            order.customer_name,
        )
        / 100.0
    )

    return (
        _PHONE_WEIGHT * phone_signal
        + _NAME_WEIGHT * name_signal
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
    unrecognized values return UNKNOWN.

    Unknown status does not make an order unusable, so it is represented
    honestly instead of guessed or rejected.
    """
    cleaned = _clean_optional_text(raw)

    if cleaned is None:
        return OrderStatus.UNKNOWN

    return _STATUS_MAP.get(
        cleaned.lower(),
        OrderStatus.UNKNOWN,
    )


def normalize_phone(raw: str | None) -> str | None:
    """
    Normalize a legacy phone into one canonical form: "+234" plus the last
    ten significant digits.

    The legacy formats all wrap the same ten digits, so stripping to digits
    and taking the last ten collapses them to one comparable representation.

    A value that cannot yield ten digits returns None rather than raising.
    """
    text = _clean_optional_text(raw)

    if text is None:
        return None

    digits = "".join(
        character
        for character in text
        if character.isdigit()
    )

    if len(digits) < 10:
        return None

    return f"+234{digits[-10:]}"


def parse_amount(raw: str | int) -> Decimal | None:
    """
    Parse a legacy amount into a money-safe Decimal.

    The amount column holds either a formatted string such as "N162,500"
    or a bare integer such as 54550.

    Amounts are whole naira with no kobo, so stripping to digits is safe
    for this source data.

    Returns Decimal, never float, so currency remains exact.
    """
    text = str(raw).strip()

    digits = "".join(
        character
        for character in text
        if character.isdigit()
    )

    if not digits:
        return None

    return Decimal(digits)


def build_order(
    row: dict,
) -> CanonicalOrder | RejectedRow:
    """
    Build a CanonicalOrder from one raw legacy order row, or quarantine it.

    Required fields are:
    - name
    - phone
    - amount
    - date

    Any failed required field causes the row to be quarantined. All failures
    are reported together so the rejected row contains the full correction
    picture.

    Status is soft and always resolves to an OrderStatus.
    """
    name = normalize_name(
        row.get("cust_name"),
        row.get("customer"),
    )
    order_date = parse_order_date(
        row.get("order_date")
    )
    phone = normalize_phone(
        row.get("phone")
    )
    amount = parse_amount(
        row.get("amount")
    )
    status = normalize_status(
        row.get("status")
    )

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
    Build a CanonicalReturn from one raw returns row, or quarantine it.

    Returns do not have an order_id, so return_row_index identifies the
    source row.

    Required fields are:
    - name
    - phone
    - amount

    `reason` is soft. Blank or missing reasons are stored as an empty
    string rather than causing rejection.
    """
    name = normalize_name(
        row.get("customer_name"),
        None,
    )
    phone = normalize_phone(
        row.get("phone")
    )
    amount = parse_amount(
        row.get("amount")
    )
    reason = (
        _clean_optional_text(
            row.get("reason")
        )
        or ""
    )

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

    Clean orders and returns go to their respective buckets. Rejected rows from
    both sources are pooled into the shared `rejected` collection.

    Returns are indexed by their position in the input list because the source
    does not provide an order_id.
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
        built = build_return(
            row,
            index,
        )

        if isinstance(built, RejectedRow):
            rejected.append(built)
        else:
            returns.append(built)

    return ReconciliationResult(
        orders=orders,
        returns=returns,
        rejected=rejected,
    )