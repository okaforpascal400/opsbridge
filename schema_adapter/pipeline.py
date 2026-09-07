# schema_adapter/pipeline.py
"""
The I/O shell around the pure reconciliation logic.

reconcile.py knows nothing about databases or files: it takes plain rows and returns
plain objects, which is what makes it fully testable. This module is the only place that
touches the real sources. It reads the legacy orders from Postgres and the returns from
the committed CSV (the two sources share no key, which is why fuzzy matching exists),
runs reconcile over them, matches every clean return to the orders, and reports the
aggregate numbers.

Run it directly to see those numbers against the seeded data:
    python -m schema_adapter.pipeline
"""
from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from config import get_settings
from schema_adapter.models import (
    MatchConfidence,
    ReconciliationResult,
    RejectedRow,
    ReturnMatch,
)
from schema_adapter.reconcile import match_return_to_orders, reconcile

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RETURNS_CSV = REPO_ROOT / "data" / "returns.csv"

_SELECT_ORDERS_SQL = (
    "SELECT order_id, cust_name, customer, order_date, status, phone, amount "
    "FROM legacy.orders ORDER BY order_id"
)


class PipelineSummary(BaseModel):
    """The aggregate picture of one full pipeline run."""

    model_config = ConfigDict(extra="forbid")

    orders_in: int = Field(ge=0)
    orders_canonicalized: int = Field(ge=0)
    orders_quarantined: int = Field(ge=0)
    returns_in: int = Field(ge=0)
    returns_canonicalized: int = Field(ge=0)
    returns_quarantined: int = Field(ge=0)
    matched_high: int = Field(ge=0)
    matched_low: int = Field(ge=0)
    matched_none: int = Field(ge=0)


def read_order_rows(engine: Engine) -> list[dict]:
    """Read every legacy order row from Postgres as a plain dict."""
    with engine.connect() as connection:
        result = connection.execute(text(_SELECT_ORDERS_SQL))
        return [dict(row) for row in result.mappings()]


def read_return_rows(csv_path: Path | None = None) -> list[dict]:
    """Read every return row from the committed CSV export as a plain dict."""
    path = csv_path or DEFAULT_RETURNS_CSV
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _is_order_reject(rejected: RejectedRow) -> bool:
    """A rejected row came from the orders source if its raw source has an order_id."""
    return "order_id" in rejected.source


def run_pipeline(
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> tuple[PipelineSummary, ReconciliationResult, list[ReturnMatch]]:
    """
    Run the full pipeline against the real sources and return the summary, the
    reconciliation result, and the per-return matches.

    Orders come from Postgres, returns from the CSV. Both are reconciled, then every
    clean return is matched against the clean orders.
    """
    url = database_url or get_settings().database_url

    engine = create_engine(url)
    try:
        order_rows = read_order_rows(engine)
    finally:
        engine.dispose()

    return_rows = read_return_rows(returns_csv)

    result = reconcile(order_rows, return_rows)

    matches = [match_return_to_orders(ret, result.orders) for ret in result.returns]

    order_rejects = [r for r in result.rejected if _is_order_reject(r)]
    return_rejects = [r for r in result.rejected if not _is_order_reject(r)]

    summary = PipelineSummary(
        orders_in=len(order_rows),
        orders_canonicalized=len(result.orders),
        orders_quarantined=len(order_rejects),
        returns_in=len(return_rows),
        returns_canonicalized=len(result.returns),
        returns_quarantined=len(return_rejects),
        matched_high=sum(1 for m in matches if m.confidence == MatchConfidence.HIGH),
        matched_low=sum(1 for m in matches if m.confidence == MatchConfidence.LOW),
        matched_none=sum(1 for m in matches if m.confidence == MatchConfidence.NONE),
    )
    return summary, result, matches


def main() -> None:
    """Print the pipeline summary against the configured database and CSV."""
    summary, _result, _matches = run_pipeline()
    print("OpsBridge reconciliation summary")
    print("--------------------------------")
    print(f"orders in:            {summary.orders_in}")
    print(f"  canonicalized:      {summary.orders_canonicalized}")
    print(f"  quarantined:        {summary.orders_quarantined}")
    print(f"returns in:           {summary.returns_in}")
    print(f"  canonicalized:      {summary.returns_canonicalized}")
    print(f"  quarantined:        {summary.returns_quarantined}")
    print("return matches:")
    print(f"  HIGH (auto-accept): {summary.matched_high}")
    print(f"  LOW  (review):      {summary.matched_low}")
    print(f"  NONE (no match):    {summary.matched_none}")


if __name__ == "__main__":
    main()
