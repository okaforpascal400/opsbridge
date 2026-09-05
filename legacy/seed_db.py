"""Load the seeded legacy mess into Postgres and export the returns spreadsheet.

This module only moves data: every row it writes comes from `legacy.seed_data`,
which is the single source of truth for what the mess looks like.

Running the seed is destructive. `create_schema` drops the whole `legacy` schema
before recreating it, so re-running gives byte-identical rows but also throws away
anything else that was living in that schema. That is why the database tests point
at a separate throwaway database via OPSBRIDGE_TEST_DATABASE_URL instead of the
development one.

Run it with:

    python -m legacy.seed_db
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from config import get_settings
from legacy.seed_data import (
    RETURNS_CSV_COLUMNS,
    LegacyOrderRow,
    ReturnRow,
    build_order_rows,
    build_return_rows,
)

LEGACY_SCHEMA: Final[str] = "legacy"
ORDERS_TABLE: Final[str] = "orders"
REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_RETURNS_CSV: Final[Path] = REPO_ROOT / "data" / "returns.csv"

# The only interpolation into SQL anywhere in this module is these two module
# constants as identifiers. Every value goes through bound parameters.
_DROP_SCHEMA_SQL: Final[str] = f"DROP SCHEMA IF EXISTS {LEGACY_SCHEMA} CASCADE"
_CREATE_SCHEMA_SQL: Final[str] = f"CREATE SCHEMA {LEGACY_SCHEMA}"
_CREATE_ORDERS_SQL: Final[str] = f"""
CREATE TABLE {LEGACY_SCHEMA}.{ORDERS_TABLE} (
    order_id    INTEGER PRIMARY KEY,
    cust_name   TEXT NULL,
    customer    TEXT NULL,
    -- order_date, status and amount are TEXT on purpose. The source system stores
    -- mixed date formats, free-text statuses including empty strings, and amounts
    -- written both as "N12,500" and as "12500". Postgres cannot hold those shapes
    -- in a typed column, and normalising them is Phase 2's job, not the seed's.
    -- This is the seeded mess, not an oversight: do not "fix" it to DATE/NUMERIC.
    order_date  TEXT NOT NULL,
    status      TEXT NOT NULL,
    phone       TEXT NOT NULL,
    amount      TEXT NOT NULL
)
"""
_INSERT_ORDERS_SQL: Final[str] = f"""
INSERT INTO {LEGACY_SCHEMA}.{ORDERS_TABLE}
    (order_id, cust_name, customer, order_date, status, phone, amount)
VALUES
    (:order_id, :cust_name, :customer, :order_date, :status, :phone, :amount)
"""


class SeedResult(BaseModel):
    """What one seed run produced, for the CLI summary and for tests."""

    orders_inserted: int
    returns_written: int
    returns_csv_path: str


def create_schema(engine: Engine) -> None:
    """Drop and recreate the legacy schema and its orders table.

    Destructive by design: dropping first is what makes the seed idempotent.
    """
    with engine.begin() as conn:
        conn.execute(text(_DROP_SCHEMA_SQL))
        conn.execute(text(_CREATE_SCHEMA_SQL))
        conn.execute(text(_CREATE_ORDERS_SQL))


def insert_orders(engine: Engine, rows: Sequence[LegacyOrderRow]) -> int:
    """Insert the order rows in one transaction and return how many were written."""
    if not rows:
        return 0

    payload = [row.model_dump() for row in rows]
    with engine.begin() as conn:
        conn.execute(text(_INSERT_ORDERS_SQL), payload)

    # rowcount is driver-dependent for executemany, so report the payload size:
    # the transaction committed, so every row in it landed.
    return len(payload)


def write_returns_csv(rows: Sequence[ReturnRow], path: Path) -> Path:
    """Write the returns export to `path` and return that path.

    The header is fixed by RETURNS_CSV_COLUMNS and carries no order_id: name plus
    phone is the only join path back to the orders table, by design.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        # csv defaults to CRLF on every platform. The export is committed, so it is
        # pinned to LF here and in .gitattributes: otherwise reseeding on Linux or
        # macOS rewrites every line of a file that has not actually changed.
        writer = csv.DictWriter(
            handle, fieldnames=RETURNS_CSV_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(row.model_dump() for row in rows)
    return path


def seed(database_url: str | None = None, returns_csv: Path | None = None) -> SeedResult:
    """Rebuild the legacy schema, insert the orders, and write the returns export."""
    url = database_url or get_settings().database_url
    csv_path = returns_csv or DEFAULT_RETURNS_CSV

    orders = build_order_rows()
    returns = build_return_rows(orders)

    engine = create_engine(url)
    try:
        create_schema(engine)
        inserted = insert_orders(engine, orders)
    finally:
        engine.dispose()

    written_to = write_returns_csv(returns, csv_path)
    return SeedResult(
        orders_inserted=inserted,
        returns_written=len(returns),
        returns_csv_path=str(written_to),
    )


def main() -> None:
    """Entrypoint for `python -m legacy.seed_db`."""
    try:
        result = seed()
    except SQLAlchemyError as exc:
        print(
            "Could not seed the legacy database. Start Postgres (docker compose up -d) "
            "and check that DATABASE_URL points at it. Database error: "
            f"{exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print(
        f"Seeded {result.orders_inserted} orders into {LEGACY_SCHEMA}.{ORDERS_TABLE}, "
        f"wrote {result.returns_written} returns to {result.returns_csv_path}"
    )


if __name__ == "__main__":
    main()
