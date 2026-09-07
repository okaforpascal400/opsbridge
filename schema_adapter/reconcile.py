"""
Reconciles the messy legacy sources into the clean canonical contract.

Each normalizer is a small, pure, individually testable function.
Row builders run the normalizers and either produce a canonical object
or quarantine the row.

Returns can then be matched against canonical orders using explicit,
defensible scoring rules. Ambiguous matches are flagged for human review
rather than silently guessed.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from rapidfuzz import fuzz

from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    MatchConfidence,
    OrderStatus,
    ReconciliationResult,
    RejectedRow,
    ReturnMatch,
)

# ---------------------------------------------------------------------------
# Status normalization
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, OrderStatus] = {
    "confirmed": OrderStatus.CONFIRMED,
    "conf": OrderStatus.CONFIRMED,
    "pending call": OrderStatus.PENDING,
    "pending": OrderStatus.PENDING,
    "delivered": OrderStatus.DELIVERED,
    "returned?": OrderStatus.RETURNED,
}


# ---------------------------------------------------------------------------
# Return-to-order matching policy
# ---------------------------------------------------------------------------

# Weights for combining the two identity signals. Phone dominates because
# normalize_phone makes exact phone comparison reliable, while names are
# deliberately noisy in the data, so name similarity confirms rather than decides.
_PHONE_WEIGHT: float = 0.7
_NAME_WEIGHT: float = 0.3

# Thresholds that turn a blended score into a verdict.
#
# A phone match alone yields 0.7, so HIGH requires phone plus a strong name
# match. A bare phone match lands in LOW and is flagged for human review.
_HIGH_THRESHOLD: float = 0.85
_LOW_THRESHOLD: float = 0.5


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
    to 0.0-1.0. It contributes a smaller share and confirms or weakens
    the match.

    A phone match with a differing name therefore scores higher than a
    name match with a differing phone.
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


def match_return_to_orders(
    ret: CanonicalReturn,
    orders: list[CanonicalOrder],
) -> ReturnMatch:
    """
    Find the best-matching order for a return and report how confident the match is.

    Every candidate is scored with _score_candidate; the highest-scoring order wins.

    The score becomes a verdict:
    - HIGH: auto-accept
    - LOW: flag for human review
    - NONE: no plausible match

    When evidence is only partial, the matcher flags LOW rather than guessing.
    An honest "not sure" is safer than a silent wrong match.
    """
    if not orders:
        return ReturnMatch(
            return_row=ret.return_row,
            matched_order_id=None,
            confidence=MatchConfidence.NONE,
            score=0.0,
            rationale="No candidate orders to match against.",
        )

    best_order = max(
        orders,
        key=lambda order: _score_candidate(ret, order),
    )

    best_score = _score_candidate(
        ret,
        best_order,
    )

    if best_score >= _HIGH_THRESHOLD:
        confidence = MatchConfidence.HIGH
        matched_id = best_order.order_id
        rationale = (
            f"Strong match to order {best_order.order_id}: "
            f"phone and name both align (score {best_score:.2f})."
        )

    elif best_score >= _LOW_THRESHOLD:
        confidence = MatchConfidence.LOW
        matched_id = best_order.order_id
        rationale = (
            f"Partial match to order {best_order.order_id}: "
            f"evidence is incomplete (score {best_score:.2f}). "
            "Flagged for human review."
        )

    else:
        confidence = MatchConfidence.NONE
        matched_id = None
        rationale = (
            f"No plausible match "
            f"(best score {best_score:.2f})."
        )

    return ReturnMatch(
        return_row=ret.return_row,
        matched_order_id=matched_id,
        confidence=confidence,
        score=best_score,
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def _clean_optional_text(
    value: str | None,
) -> str | None:
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
    return (
        _clean_optional_text(cust_name)
        or _clean_optional_text(customer)
    )


def parse_order_date(
    raw: str,
) -> date | None:
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
            return datetime.strptime(
                text,
                fmt,
            ).date()
        except ValueError:
            continue

    return None


def normalize_status(
    raw: str | None,
) -> OrderStatus:
    """
    Map a legacy free-text status onto the OrderStatus enum.

    Matching is case-insensitive and whitespace-tolerant.

    Blank and unrecognized values return UNKNOWN. An unknown status does
    not make an order unusable, so we record the uncertainty instead of
    guessing or rejecting the row.
    """
    cleaned = _clean_optional_text(raw)

    if cleaned is None:
        return OrderStatus.UNKNOWN

    return _STATUS_MAP.get(
        cleaned.lower(),
        OrderStatus.UNKNOWN,
    )


def normalize_phone(
    raw: str | None,
) -> str | None:
    """
    Normalize a legacy phone into one canonical form: "+234" plus the
    last ten significant digits.

    The legacy formats all wrap the same ten digits, so stripping to
    digits and taking the last ten collapses them into one comparable
    representation.

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


def parse_amount(
    raw: str | int,
) -> Decimal | None:
    """
    Parse a legacy amount into a money-safe Decimal.

    The amount column holds either a formatted value such as "N162,500"
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


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def build_order(
    row: dict,
) -> CanonicalOrder | RejectedRow:
    """
    Build a CanonicalOrder from one raw legacy order row, or quarantine it.

    Required fields are name, phone, amount, and date.

    Any failed required field causes the row to be quarantined. All
    failures are reported together so the rejected row contains the
    complete correction picture.

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

    Returns have no order_id, so return_row_index identifies the source row.

    Required fields are name, phone, and amount. Any failed required field
    quarantines the row.

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


# ---------------------------------------------------------------------------
# Reconciliation entrypoint
# ---------------------------------------------------------------------------

def reconcile(
    order_rows: list[dict],
    return_rows: list[dict],
) -> ReconciliationResult:
    """
    Run every raw row through its builder and collect the outcomes into one result.

    Clean orders and returns go to their respective buckets. Rejected rows
    from both sources are pooled into the shared `rejected` collection.

    Returns are indexed by their position in the input list because their
    source does not provide an order_id.

    Matching is deliberately separate from reconciliation. This function
    establishes trustworthy canonical data first.
    """
    orders: list[CanonicalOrder] = []
    returns: list[CanonicalReturn] = []
    rejected: list[RejectedRow] = []

    for row in order_rows:
        built = build_order(row)

        if isinstance(
            built,
            RejectedRow,
        ):
            rejected.append(built)
        else:
            orders.append(built)

    for index, row in enumerate(return_rows):
        built = build_return(
            row,
            index,
        )

        if isinstance(
            built,
            RejectedRow,
        ):
            rejected.append(built)
        else:
            returns.append(built)

    return ReconciliationResult(
        orders=orders,
        returns=returns,
        rejected=rejected,
    )