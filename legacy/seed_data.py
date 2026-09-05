"""Deterministic generator for the legacy operations mess.

This module is the single source of truth for what the fragmented stack looks
like. It builds rows in memory only: no database, no filesystem, no network, so
it can be imported by the seeder, by the partner API fixture and by tests alike.

Two properties matter more than anything else here.

Determinism. Every draw goes through a `random.Random(seed)` instance. Nothing
reads the clock, the process entropy pool or a UUID. Calling `build_order_rows()`
twice in the same process, or in two different processes, returns identical rows.

Coverage by construction. The messy variants (date formats, phone formats, amount
shapes, statuses, which name column is populated) are not left to chance. For each
varying field a plan is built that holds the intended number of slots per variant,
the plan is shuffled with the seeded generator, and it is then zipped with the
rows. Every variant is therefore guaranteed present whatever the seed is, so tests
assert on structure rather than on a lucky draw.

About the deliberate damage:

* `amount` is TEXT and holds two shapes in the same column, "N12,500" and "12500".
  A single TEXT column is the whole point. Postgres cannot hold both shapes in a
  NUMERIC column, and a real export out of a legacy tool routinely looks exactly
  like this. Normalising it is the schema adapter's job in Phase 2, not this
  module's.
* `order_date` is TEXT in three formats, one of which ("01/03/2026") is genuinely
  ambiguous between day/month and month/day. That ambiguity is a real
  reconciliation problem, kept on purpose.
* The returns export carries no order id. Name plus phone is the only join path.
  Every phone is rewritten into a different format and most names are misspelled, so
  a raw string join finds almost nothing. A deliberate minority of names are left
  exact, so a matcher cannot assume every name was mangled either.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict

RANDOM_SEED: Final[int] = 20260901
ORDER_COUNT: Final[int] = 200
RETURN_COUNT: Final[int] = 40
RETURNS_CSV_COLUMNS: Final[tuple[str, ...]] = ("customer_name", "phone", "amount", "reason")

FIRST_NAMES: Final[tuple[str, ...]] = (
    "Chidi", "Ngozi", "Emeka", "Aisha", "Tunde", "Folake", "Ifeoma", "Yusuf",
    "Adaeze", "Segun", "Amaka", "Bola", "Ibrahim", "Kelechi", "Nneka", "Obinna",
    "Halima", "Femi", "Uche", "Zainab",
)
MIDDLE_NAMES: Final[tuple[str, ...]] = (
    "Chukwuemeka", "Oluwaseun", "Adebayo", "Ifeanyi", "Olamide", "Nkechi",
    "Abiodun", "Chinelo", "Musa", "Temitope",
)
SURNAMES: Final[tuple[str, ...]] = (
    "Okafor", "Adeyemi", "Balogun", "Nwosu", "Eze", "Okonkwo", "Bello", "Adewale",
    "Ogunleye", "Chukwu", "Danjuma", "Olawale", "Nwachukwu", "Abubakar",
    "Oyelaran", "Ezenwa", "Akinyemi", "Uzoma", "Lawal", "Onyeka",
)

MONTH_NAMES: Final[tuple[str, ...]] = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
DATE_RANGE_START: Final[date] = date(2026, 1, 1)
DATE_RANGE_END: Final[date] = date(2026, 6, 30)
DATE_FORMATS: Final[tuple[str, ...]] = ("iso", "slashed", "long")
DATE_FORMAT_WEIGHTS: Final[tuple[int, ...]] = (40, 32, 28)

# Free text typed by whoever was on the phone that day, casing and all.
STATUS_VALUES: Final[tuple[str, ...]] = (
    "confirmed", "CONF", "pending call", "PENDING", "delivered", "returned?", "",
)
STATUS_WEIGHTS: Final[tuple[int, ...]] = (28, 14, 16, 10, 22, 5, 5)

PHONE_PREFIXES: Final[tuple[str, ...]] = (
    "0803", "0806", "0703", "0813", "0902", "0810", "0805", "0816", "0701", "0907",
)
PHONE_FORMATS: Final[tuple[str, ...]] = ("international", "national", "bare")
PHONE_FORMAT_WEIGHTS: Final[tuple[int, ...]] = (38, 37, 25)

AMOUNT_FORMATS: Final[tuple[str, ...]] = ("naira_text", "bare_integer")
AMOUNT_FORMAT_WEIGHTS: Final[tuple[int, ...]] = (55, 45)
AMOUNT_MINIMUM: Final[int] = 1500
AMOUNT_MAXIMUM: Final[int] = 250000
AMOUNT_STEP: Final[int] = 50

# Exactly one of these two columns is populated per row. Two half-migrated columns
# for the same fact is the kind of thing nobody ever went back and cleaned up.
NAME_FIELDS: Final[tuple[str, ...]] = ("cust_name", "customer")
NAME_FIELD_WEIGHTS: Final[tuple[int, ...]] = (85, 15)

MIDDLE_NAME_PRESENCE: Final[tuple[bool, ...]] = (True, False)
MIDDLE_NAME_WEIGHTS: Final[tuple[int, ...]] = (48, 52)

RETURN_REASONS: Final[tuple[str, ...]] = (
    "damaged on arrival", "wrong item", "late delivery", "customer changed mind",
    "duplicate order", "quality issue",
)
NAME_VARIATIONS: Final[tuple[str, ...]] = (
    "drop_middle", "transpose", "drop_vowel", "double_letter", "unchanged",
)
NAME_VARIATION_WEIGHTS: Final[tuple[int, ...]] = (30, 25, 18, 15, 12)

REFUND_SHAPES: Final[tuple[str, ...]] = ("full", "partial")
REFUND_SHAPE_WEIGHTS: Final[tuple[int, ...]] = (55, 45)
PARTIAL_REFUND_PERCENTS: Final[tuple[int, ...]] = (25, 40, 50, 60, 75)

VOWELS: Final[str] = "aeiouAEIOU"


class LegacyOrderRow(BaseModel):
    """One row of legacy.orders, already in its damaged string form.

    Every field except `order_id` is text, because that is how the source system
    stores it. Nothing here is normalised, on purpose.
    """

    model_config = ConfigDict(frozen=True)

    order_id: int
    cust_name: str | None
    customer: str | None
    order_date: str
    status: str
    phone: str
    amount: str


class ReturnRow(BaseModel):
    """One row of the returns spreadsheet export.

    There is no order id. Name and phone are the only join path, and both were
    re-keyed by hand somewhere upstream, so neither matches the order exactly.
    """

    model_config = ConfigDict(frozen=True)

    customer_name: str
    phone: str
    amount: str
    reason: str


def _allocate_counts(weights: Sequence[int], total: int) -> list[int]:
    """Scale relative weights into slot counts summing to `total`, minimum one each.

    The minimum of one is what makes coverage a property of the plan rather than of
    the seed: a variant with a small weight still gets a slot.
    """
    if not weights:
        raise ValueError("weights must not be empty")
    if total < len(weights):
        raise ValueError(f"total {total} cannot cover {len(weights)} variants at least once")
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive number")

    counts = [max(1, weight * total // weight_sum) for weight in weights]
    largest_first = sorted(range(len(counts)), key=lambda index: counts[index], reverse=True)

    # Rounding drift lands on the biggest buckets, so no variant is squeezed to zero.
    position = 0
    while sum(counts) < total:
        counts[largest_first[position % len(counts)]] += 1
        position += 1
    position = 0
    while sum(counts) > total:
        index = largest_first[position % len(counts)]
        if counts[index] > 1:
            counts[index] -= 1
        position += 1
    return counts


def _variant_plan[T](
    variants: Sequence[T],
    weights: Sequence[int],
    total: int,
    rng: random.Random,
) -> list[T]:
    """Return `total` shuffled variant slots with every variant guaranteed present."""
    if len(variants) != len(weights):
        raise ValueError("variants and weights must be the same length")
    plan: list[T] = []
    for variant, slots in zip(variants, _allocate_counts(weights, total), strict=True):
        plan.extend([variant] * slots)
    rng.shuffle(plan)
    return plan


def _pick_date(rng: random.Random) -> date:
    """Pick a date inside the fixture window, both ends inclusive."""
    span_days = (DATE_RANGE_END - DATE_RANGE_START).days
    return DATE_RANGE_START + timedelta(days=rng.randint(0, span_days))


def _format_date(value: date, style: str) -> str:
    """Render a date in one of the three formats the source system mixes."""
    if style == "iso":
        return value.isoformat()
    if style == "slashed":
        return f"{value.day:02d}/{value.month:02d}/{value.year}"
    if style == "long":
        # Built by hand from MONTH_NAMES: strftime("%-d") is not portable to Windows.
        return f"{MONTH_NAMES[value.month - 1]} {value.day}, {value.year}"
    raise ValueError(f"unknown date style: {style}")


def _pick_local_number(rng: random.Random) -> str:
    """Return a ten digit Nigerian mobile number, with no country code or leading zero."""
    prefix = rng.choice(PHONE_PREFIXES).removeprefix("0")
    return prefix + "".join(str(rng.randint(0, 9)) for _ in range(7))


def _format_phone(local_number: str, style: str) -> str:
    """Render the same ten digit number in one of the three formats in use."""
    if style == "international":
        return f"+234{local_number}"
    if style == "national":
        return f"0{local_number}"
    if style == "bare":
        return local_number
    raise ValueError(f"unknown phone style: {style}")


def _detect_phone_format(phone: str) -> str:
    """Classify a phone string back into one of PHONE_FORMATS."""
    if phone.startswith("+234"):
        return "international"
    if phone.startswith("0"):
        return "national"
    return "bare"


def _local_number_of(phone: str) -> str:
    """Strip a phone back to its ten significant digits."""
    digits = "".join(character for character in phone if character.isdigit())
    if len(digits) < 10:
        raise ValueError(f"phone holds fewer than ten digits: {phone!r}")
    return digits[-10:]


def _pick_amount(rng: random.Random) -> int:
    """Pick an order value in whole naira, rounded to a plausible step."""
    return rng.randrange(AMOUNT_MINIMUM, AMOUNT_MAXIMUM + 1, AMOUNT_STEP)


def _format_amount(value: int, style: str) -> str:
    """Render a naira value in one of the two shapes that share the amount column."""
    if style == "naira_text":
        return f"N{value:,}"
    if style == "bare_integer":
        return str(value)
    raise ValueError(f"unknown amount style: {style}")


def _parse_amount(text: str) -> int:
    """Read a whole naira value back out of either amount shape."""
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        raise ValueError(f"amount holds no digits: {text!r}")
    return int(digits)


def _build_name(with_middle: bool, rng: random.Random) -> str:
    """Assemble a customer name, with or without a middle name."""
    parts = [rng.choice(FIRST_NAMES)]
    if with_middle:
        parts.append(rng.choice(MIDDLE_NAMES))
    parts.append(rng.choice(SURNAMES))
    return " ".join(parts)


def _split_name_fields(name: str, field: str) -> tuple[str | None, str | None]:
    """Return (cust_name, customer) with exactly one of them populated.

    Keeping the XOR in one place is what lets a test assert it for every row.
    """
    if field == "cust_name":
        return name, None
    if field == "customer":
        return None, name
    raise ValueError(f"unknown name field: {field}")


def _populated_name(row: LegacyOrderRow) -> str:
    """Return whichever of the two name columns actually holds the name."""
    name = row.cust_name if row.cust_name is not None else row.customer
    if name is None:
        raise ValueError(f"order {row.order_id} has no populated name")
    return name


def build_order_rows(count: int = ORDER_COUNT, seed: int = RANDOM_SEED) -> list[LegacyOrderRow]:
    """Build the legacy order rows, deterministically and with every variant present.

    Order ids run from 1 to `count`. Every variant plan is drawn up front so the
    shuffles depend only on the seed and the row count.
    """
    if count < 1:
        raise ValueError("count must be at least 1")

    rng = random.Random(seed)
    name_fields = _variant_plan(NAME_FIELDS, NAME_FIELD_WEIGHTS, count, rng)
    middle_names = _variant_plan(MIDDLE_NAME_PRESENCE, MIDDLE_NAME_WEIGHTS, count, rng)
    date_styles = _variant_plan(DATE_FORMATS, DATE_FORMAT_WEIGHTS, count, rng)
    statuses = _variant_plan(STATUS_VALUES, STATUS_WEIGHTS, count, rng)
    phone_styles = _variant_plan(PHONE_FORMATS, PHONE_FORMAT_WEIGHTS, count, rng)
    amount_styles = _variant_plan(AMOUNT_FORMATS, AMOUNT_FORMAT_WEIGHTS, count, rng)

    rows: list[LegacyOrderRow] = []
    for index in range(count):
        name = _build_name(middle_names[index], rng)
        cust_name, customer = _split_name_fields(name, name_fields[index])
        rows.append(
            LegacyOrderRow(
                order_id=index + 1,
                cust_name=cust_name,
                customer=customer,
                order_date=_format_date(_pick_date(rng), date_styles[index]),
                status=statuses[index],
                phone=_format_phone(_pick_local_number(rng), phone_styles[index]),
                amount=_format_amount(_pick_amount(rng), amount_styles[index]),
            )
        )
    return rows


def _double_letter(surname: str, rng: random.Random) -> str:
    """Repeat one letter of the surname, the classic re-keying slip."""
    index = rng.randint(1, len(surname) - 1)
    return surname[: index + 1] + surname[index] + surname[index + 1 :]


def _transpose_surname(surname: str, rng: random.Random) -> str:
    """Swap two adjacent, different letters, leaving the leading capital alone."""
    positions = [
        index for index in range(1, len(surname) - 1) if surname[index] != surname[index + 1]
    ]
    if not positions:
        return _double_letter(surname, rng)
    index = rng.choice(positions)
    return surname[:index] + surname[index + 1] + surname[index] + surname[index + 2 :]


def _drop_vowel(surname: str) -> str:
    """Drop the last vowel, which is the trailing one whenever the surname ends in one."""
    for index in range(len(surname) - 1, 0, -1):
        if surname[index] in VOWELS:
            return surname[:index] + surname[index + 1 :]
    return surname


def _vary_name(name: str, variation: str, rng: random.Random) -> str:
    """Apply one deliberate name variation so the join is fuzzy rather than exact."""
    if variation == "unchanged":
        return name

    parts = name.split(" ")
    if variation == "drop_middle":
        if len(parts) < 3:
            raise ValueError(f"cannot drop a middle name from {name!r}")
        return f"{parts[0]} {parts[-1]}"

    surname = parts[-1]
    if variation == "transpose":
        varied = _transpose_surname(surname, rng)
    elif variation == "drop_vowel":
        varied = _drop_vowel(surname)
    elif variation == "double_letter":
        varied = _double_letter(surname, rng)
    else:
        raise ValueError(f"unknown name variation: {variation}")
    return " ".join([*parts[:-1], varied])


def _pair_orders_with_variations(
    orders: Sequence[LegacyOrderRow],
    count: int,
    rng: random.Random,
) -> list[tuple[LegacyOrderRow, str]]:
    """Pick `count` distinct orders and decide which name variation each one gets.

    Orders carrying a middle name are reserved for the drop-middle variation first,
    so that variation is guaranteed rather than dependent on the draw.
    """
    if count < 1:
        raise ValueError("count must be at least 1")
    if count > len(orders):
        raise ValueError(f"cannot pick {count} distinct orders from {len(orders)}")

    plan = _variant_plan(NAME_VARIATIONS, NAME_VARIATION_WEIGHTS, count, rng)
    drop_slots = plan.count("drop_middle")
    with_middle = [row for row in orders if len(_populated_name(row).split(" ")) >= 3]
    if drop_slots > len(with_middle):
        raise ValueError(f"only {len(with_middle)} orders carry a middle name, need {drop_slots}")

    reserved = rng.sample(with_middle, drop_slots)
    reserved_ids = {row.order_id for row in reserved}
    remainder = [row for row in orders if row.order_id not in reserved_ids]
    others = rng.sample(remainder, count - drop_slots)

    reserved_queue = iter(reserved)
    other_queue = iter(others)
    paired: list[tuple[LegacyOrderRow, str]] = []
    for variation in plan:
        row = next(reserved_queue) if variation == "drop_middle" else next(other_queue)
        paired.append((row, variation))
    return paired


def _reformat_phone(order_phone: str, rng: random.Random) -> str:
    """Re-format a phone into one of the other two formats.

    The returns file came out of a different tool, so the same subscriber is written
    a different way. A raw string join therefore fails, which is the point: matching
    has to normalise first.
    """
    current = _detect_phone_format(order_phone)
    alternatives = [style for style in PHONE_FORMATS if style != current]
    return _format_phone(_local_number_of(order_phone), rng.choice(alternatives))


def _refund_value(order_amount: str, refund_shape: str, rng: random.Random) -> int:
    """Return the refunded naira value, which is often less than the order value."""
    order_value = _parse_amount(order_amount)
    if refund_shape == "full":
        return order_value
    if refund_shape == "partial":
        percent = rng.choice(PARTIAL_REFUND_PERCENTS)
        partial = order_value * percent // 100 // AMOUNT_STEP * AMOUNT_STEP
        return max(AMOUNT_STEP, partial)
    raise ValueError(f"unknown refund shape: {refund_shape}")


def build_return_rows(
    orders: Sequence[LegacyOrderRow],
    count: int = RETURN_COUNT,
    seed: int = RANDOM_SEED,
) -> list[ReturnRow]:
    """Build the returns export rows from a sample of orders.

    Each row points at a real order without being a copy of one. The phone is always
    rewritten into one of the other two formats and the amount is often a partial
    refund. The name is misspelled for every variation except the deliberate
    "unchanged" minority, which keeps the order's name verbatim so a matcher cannot
    assume every name was mangled. There is no order id column, by design.
    """
    rng = random.Random(seed)
    paired = _pair_orders_with_variations(orders, count, rng)
    amount_styles = _variant_plan(AMOUNT_FORMATS, AMOUNT_FORMAT_WEIGHTS, count, rng)
    refund_shapes = _variant_plan(REFUND_SHAPES, REFUND_SHAPE_WEIGHTS, count, rng)

    rows: list[ReturnRow] = []
    for index, (order, variation) in enumerate(paired):
        refund = _refund_value(order.amount, refund_shapes[index], rng)
        rows.append(
            ReturnRow(
                customer_name=_vary_name(_populated_name(order), variation, rng),
                phone=_reformat_phone(order.phone, rng),
                amount=_format_amount(refund, amount_styles[index]),
                reason=rng.choice(RETURN_REASONS),
            )
        )
    return rows
