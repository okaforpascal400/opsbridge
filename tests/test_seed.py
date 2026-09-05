"""Tests for the seeded legacy mess.

Two layers live here. The pure tests run everywhere and pin the shape of the
fixture data. The database tests run the real seed and only execute when
OPSBRIDGE_TEST_DATABASE_URL names a throwaway Postgres database, because the
seed drops and recreates the legacy schema.

The assertions are deliberately written so that "tidying up" the fixture data
breaks them. Mixed date formats, mixed amount shapes and empty statuses are the
product requirement for Phase 2, not defects.
"""

from __future__ import annotations

import csv
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

import pytest
from sqlalchemy import Engine, create_engine, text

from legacy.seed_data import (
    ORDER_COUNT,
    RETURN_COUNT,
    RETURNS_CSV_COLUMNS,
    LegacyOrderRow,
    ReturnRow,
    build_order_rows,
    build_return_rows,
)
from legacy.seed_db import (
    DEFAULT_RETURNS_CSV,
    LEGACY_SCHEMA,
    ORDERS_TABLE,
    seed,
    write_returns_csv,
)

TEST_DATABASE_URL_ENV: Final[str] = "OPSBRIDGE_TEST_DATABASE_URL"
ORDERS_TABLE_REF: Final[str] = f"{LEGACY_SCHEMA}.{ORDERS_TABLE}"

ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLASHED_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
LONG_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$")
NAIRA_AMOUNT_RE: Final[re.Pattern[str]] = re.compile(r"^N\d{1,3}(?:,\d{3})*$")
BARE_AMOUNT_RE: Final[re.Pattern[str]] = re.compile(r"^\d+$")
INTERNATIONAL_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"^\+234\d{10}$")
NATIONAL_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"^0\d{10}$")
BARE_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{10}$")
NON_DIGITS_RE: Final[re.Pattern[str]] = re.compile(r"\D")

EXPECTED_STATUSES: Final[frozenset[str]] = frozenset(
    {"confirmed", "CONF", "pending call", "PENDING", "delivered", "returned?", ""}
)

COUNT_ROWS_SQL: Final[str] = f"SELECT count(*) FROM {ORDERS_TABLE_REF}"
COUNT_EMPTY_STATUS_SQL: Final[str] = f"SELECT count(*) FROM {ORDERS_TABLE_REF} WHERE status = ''"
SELECT_DATES_SQL: Final[str] = f"SELECT order_date FROM {ORDERS_TABLE_REF}"
COUNT_NAIRA_AMOUNTS_SQL: Final[str] = (
    f"SELECT count(*) FROM {ORDERS_TABLE_REF} "
    "WHERE amount ~ '^N[0-9]{1,3}(,[0-9]{3})*$'"
)
COUNT_SECOND_NAME_COLUMN_SQL: Final[str] = (
    f"SELECT count(*) FROM {ORDERS_TABLE_REF} WHERE cust_name IS NULL AND customer IS NOT NULL"
)


def _date_format(value: str) -> str:
    if ISO_DATE_RE.match(value):
        return "iso"
    if SLASHED_DATE_RE.match(value):
        return "slashed"
    if LONG_DATE_RE.match(value):
        return "long"
    return "unrecognised"


def _amount_shape(value: str) -> str:
    if NAIRA_AMOUNT_RE.match(value):
        return "naira"
    if BARE_AMOUNT_RE.match(value):
        return "bare"
    return "unrecognised"


def _phone_format(value: str) -> str:
    if INTERNATIONAL_PHONE_RE.match(value):
        return "international"
    if NATIONAL_PHONE_RE.match(value):
        return "national"
    if BARE_PHONE_RE.match(value):
        return "bare"
    return "unrecognised"


def _last_ten_digits(value: str) -> str:
    """Test-local phone key, kept separate from any production normaliser."""
    return NON_DIGITS_RE.sub("", value)[-10:]


def _populated_name(row: LegacyOrderRow) -> str:
    return row.cust_name or row.customer or ""


def _order_phones_by_key(orders: Sequence[LegacyOrderRow]) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = {}
    for order in orders:
        grouped.setdefault(_last_ten_digits(order.phone), set()).add(order.phone)
    return grouped


def test_build_order_rows_returns_the_declared_row_count() -> None:
    """The fixture size is a published constant, so docs and evals can rely on it."""
    assert len(build_order_rows()) == ORDER_COUNT


def test_build_order_rows_is_deterministic() -> None:
    """A reseed must not shuffle the world under the tests or the golden evals."""
    assert build_order_rows() == build_order_rows()


def test_build_return_rows_is_deterministic() -> None:
    """The returns export is derived data and has to be just as reproducible."""
    orders = build_order_rows()
    assert build_return_rows(orders) == build_return_rows(orders)


def test_every_order_populates_exactly_one_name_column() -> None:
    """Two half-filled name columns are the mess: never both, never neither."""
    broken = [
        row.order_id
        for row in build_order_rows()
        if (row.cust_name is None) == (row.customer is None)
    ]
    assert broken == [], f"rows with both or neither name column populated: {broken}"


def test_both_name_columns_carry_real_rows() -> None:
    """cust_name holds the majority, customer holds the rest, and neither is empty."""
    rows = build_order_rows()
    cust_name_rows = sum(1 for row in rows if row.cust_name is not None)
    customer_rows = sum(1 for row in rows if row.customer is not None)
    assert customer_rows > 0
    assert cust_name_rows > customer_rows


def test_every_order_name_is_non_empty() -> None:
    """A blank name would make the fuzzy join in Phase 2 meaningless rather than hard."""
    assert all(_populated_name(row).strip() for row in build_order_rows())


def test_order_ids_are_a_contiguous_range() -> None:
    """order_id is the one clean key in the orders table, so it must stay clean."""
    ids = [row.order_id for row in build_order_rows()]
    assert ids == list(range(1, ORDER_COUNT + 1))


def test_every_status_variant_appears() -> None:
    """Free-text status, including the empty string, is what the adapter must classify."""
    assert {row.status for row in build_order_rows()} == set(EXPECTED_STATUSES)


def test_all_three_date_formats_are_present() -> None:
    """Three text date formats in one column, and nothing outside those three."""
    assert {_date_format(row.order_date) for row in build_order_rows()} == {
        "iso",
        "slashed",
        "long",
    }


def test_both_amount_shapes_are_present() -> None:
    """One TEXT column holding "N12,500" and "12500" is the normalisation problem."""
    assert {_amount_shape(row.amount) for row in build_order_rows()} == {"naira", "bare"}


def test_all_three_phone_formats_are_present() -> None:
    """The same Nigerian number written three ways, so a raw string join fails."""
    assert {_phone_format(row.phone) for row in build_order_rows()} == {
        "international",
        "national",
        "bare",
    }


def test_build_return_rows_returns_the_declared_row_count() -> None:
    """The returns export is a fixed slice of the orders, not a random-sized one."""
    assert len(build_return_rows(build_order_rows())) == RETURN_COUNT


def test_returns_export_has_no_order_id_column() -> None:
    """Name plus phone is the only join path; an id column would make Phase 2 trivial."""
    assert "order_id" not in RETURNS_CSV_COLUMNS
    assert set(ReturnRow.model_fields) == set(RETURNS_CSV_COLUMNS)


def test_some_return_names_match_no_order_name() -> None:
    """Proves the name join is fuzzy: at least one name was altered on the way out."""
    orders = build_order_rows()
    order_names = {_populated_name(row) for row in orders}
    returns = build_return_rows(orders)
    assert any(row.customer_name not in order_names for row in returns)


def test_some_return_names_survive_untouched() -> None:
    """A minority stay exact, so the matcher cannot assume every name was mangled."""
    orders = build_order_rows()
    order_names = {_populated_name(row) for row in orders}
    returns = build_return_rows(orders)
    assert any(row.customer_name in order_names for row in returns)


def test_every_return_phone_normalises_onto_an_order() -> None:
    """Each return really does come from an order once the phone is normalised."""
    orders = build_order_rows()
    phones_by_key = _order_phones_by_key(orders)
    unmatched = [
        row.phone
        for row in build_return_rows(orders)
        if _last_ten_digits(row.phone) not in phones_by_key
    ]
    assert unmatched == []


def test_no_return_phone_matches_its_order_verbatim() -> None:
    """The reformatting is what makes a raw string join fail on real data."""
    orders = build_order_rows()
    phones_by_key = _order_phones_by_key(orders)
    identical = [
        row.phone
        for row in build_return_rows(orders)
        if row.phone in phones_by_key.get(_last_ten_digits(row.phone), set())
    ]
    assert identical == []


def test_return_amounts_use_both_text_shapes() -> None:
    """Refund amounts carry the same mixed text shapes as the orders table."""
    returns = build_return_rows(build_order_rows())
    assert {_amount_shape(row.amount) for row in returns} == {"naira", "bare"}


def test_return_reasons_come_from_a_small_vocabulary() -> None:
    """Reasons are short ops phrases, never blank, so they can be grouped later."""
    returns = build_return_rows(build_order_rows())
    reasons = {row.reason for row in returns}
    assert all(reason.strip() for reason in reasons)
    assert len(reasons) >= 3


def _require_test_database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(
            f"{TEST_DATABASE_URL_ENV} is not set. Point it at a throwaway Postgres "
            "database, for example "
            "postgresql+psycopg2://opsbridge:opsbridge@localhost:5432/opsbridge_test, "
            "then rerun. The seed drops and recreates the legacy schema, so never "
            "point it at a database whose contents you want to keep."
        )
    return url


def _scalar(engine: Engine, sql: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(sql)).scalar_one())


@pytest.fixture(scope="module")
def seeded_engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    """Run the real seed against the throwaway test database and hand back an engine."""
    database_url = _require_test_database_url()
    returns_csv = tmp_path_factory.mktemp("seed_db") / "returns.csv"
    seed(database_url=database_url, returns_csv=returns_csv)
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_seeded_orders_table_holds_every_row(seeded_engine: Engine) -> None:
    """The seed inserts the whole fixture, not a partial batch."""
    assert _scalar(seeded_engine, COUNT_ROWS_SQL) == ORDER_COUNT


def test_seeded_orders_keep_empty_statuses(seeded_engine: Engine) -> None:
    """An empty status must land as '' and not quietly become NULL in transit."""
    assert _scalar(seeded_engine, COUNT_EMPTY_STATUS_SQL) > 0


def test_seeded_order_dates_keep_all_three_formats(seeded_engine: Engine) -> None:
    """order_date stays TEXT, so all three formats survive the round trip."""
    with seeded_engine.connect() as connection:
        stored = [row[0] for row in connection.execute(text(SELECT_DATES_SQL))]
    assert {_date_format(value) for value in stored} == {"iso", "slashed", "long"}


def test_seeded_amounts_include_the_naira_text_shape(seeded_engine: Engine) -> None:
    """A NUMERIC column could not hold "N12,500", which is exactly why amount is TEXT."""
    assert _scalar(seeded_engine, COUNT_NAIRA_AMOUNTS_SQL) > 0


def test_seeded_rows_use_the_second_name_column(seeded_engine: Engine) -> None:
    """Some rows name the customer only in the customer column, and that survives."""
    assert _scalar(seeded_engine, COUNT_SECOND_NAME_COLUMN_SQL) > 0


def test_seed_reports_what_it_wrote(tmp_path: Path) -> None:
    """seed() is the documented entrypoint, so its summary has to match reality."""
    database_url = _require_test_database_url()
    returns_csv = tmp_path / "returns.csv"
    result = seed(database_url=database_url, returns_csv=returns_csv)
    assert result.orders_inserted == ORDER_COUNT
    assert result.returns_written == RETURN_COUNT
    assert Path(result.returns_csv_path) == returns_csv


def test_write_returns_csv_writes_a_header_and_every_row(tmp_path: Path) -> None:
    """The export is a spreadsheet drop: fixed header, one line per return, no ids."""
    rows = build_return_rows(build_order_rows())
    path = write_returns_csv(rows, tmp_path / "returns.csv")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(RETURNS_CSV_COLUMNS)
        assert len(list(reader)) == RETURN_COUNT


def test_committed_returns_export_still_matches_the_generator(tmp_path: Path) -> None:
    """data/returns.csv is committed, so it has to stay byte-identical to a fresh run.

    This also pins the line endings: the export is written LF on every platform, so a
    reseed on Linux or macOS does not rewrite a file that has not changed.
    """
    regenerated = write_returns_csv(build_return_rows(build_order_rows()), tmp_path / "returns.csv")
    assert regenerated.read_bytes() == DEFAULT_RETURNS_CSV.read_bytes()
