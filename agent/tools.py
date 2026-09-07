# agent/tools.py
"""
The agent-facing tool layer.

These are the functions the tool-calling agent is allowed to invoke. They are thin
wrappers over the pipeline: a tool never computes business logic itself, it calls the
proven reconciliation pipeline and shapes the result into plain data (dicts and lists)
that a tool-calling API can pass around. Keeping the layering strict, pure logic in
reconcile.py, I/O in pipeline.py, agent surface here, means the agent orchestrates
trustworthy functions rather than re-implementing any of their reasoning.

Phase 3 tools are read-only. Write-actions (confirm, hold, refund note) arrive in Phase 4
behind the human-in-the-loop guardrails.
"""
from __future__ import annotations

from pathlib import Path

from schema_adapter.pipeline import run_pipeline


def get_operations_summary(
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> dict:
    """
    Return the aggregate reconciliation picture as a plain dict.

    Runs the full pipeline (orders from Postgres, returns from the CSV) and reports the
    counts an operations reviewer would ask for first: how many orders and returns came
    in, how many canonicalized cleanly, how many were quarantined, and how the return
    matches break down across the confidence tiers.

    Arguments default to the configured database and committed CSV; they can be
    overridden for testing against a throwaway database.
    """
    summary, _result, _matches = run_pipeline(database_url, returns_csv)
    return {
        "orders_in": summary.orders_in,
        "orders_canonicalized": summary.orders_canonicalized,
        "orders_quarantined": summary.orders_quarantined,
        "returns_in": summary.returns_in,
        "returns_canonicalized": summary.returns_canonicalized,
        "returns_quarantined": summary.returns_quarantined,
        "matches": {
            "high_auto_accept": summary.matched_high,
            "low_needs_review": summary.matched_low,
            "none_no_match": summary.matched_none,
        },
    }
