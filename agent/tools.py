# agent/tools.py
"""
The agent-facing tool layer.

These are the functions the tool-calling agent is allowed to invoke. They are thin
wrappers over the pipeline: a tool never computes business logic itself, it calls the
proven reconciliation pipeline and shapes the result into plain data (dicts and lists)
that a tool-calling API can pass around. Keeping the layering strict, pure logic in
reconcile.py, I/O in pipeline.py, agent surface here, means the agent orchestrates
trustworthy functions rather than re-implementing any of their reasoning.

Every tool returns JSON-safe plain data, and where a record reflects a judgement (a
low-confidence match, a quarantined row) it carries the reason or rationale, because the
"why" is what makes the answer actionable for an operations reviewer.

Phase 3 tools are read-only. Write-actions (confirm, hold, refund note) arrive in Phase 4
behind the human-in-the-loop guardrails.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from schema_adapter.models import MatchConfidence
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


def get_unmatched_returns(
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> list[dict]:
    """
    Return the returns that found no plausible order match (confidence NONE).

    These are the returns an ops team cannot tie to any order in the system. Each item
    carries the return's source row index, its score, and the rationale explaining why no
    match was found.
    """
    _summary, _result, matches = run_pipeline(database_url, returns_csv)
    return [
        {
            "return_row": m.return_row,
            "score": m.score,
            "rationale": m.rationale,
        }
        for m in matches
        if m.confidence == MatchConfidence.NONE
    ]


def get_flagged_returns(
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> list[dict]:
    """
    Return the returns that matched only at LOW confidence and need human review.

    These are the ambiguous cases: partial evidence, so the matcher flagged rather than
    guessed. Each item carries the return row, the candidate order it leans toward, the
    score, and the rationale, so a reviewer can decide without re-deriving anything.
    """
    _summary, _result, matches = run_pipeline(database_url, returns_csv)
    return [
        {
            "return_row": m.return_row,
            "candidate_order_id": m.matched_order_id,
            "score": m.score,
            "rationale": m.rationale,
        }
        for m in matches
        if m.confidence == MatchConfidence.LOW
    ]


def get_quarantined_rows(
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> list[dict]:
    """
    Return the source rows that could not be canonicalized, each with its reason.

    A quarantined row is one whose required fields could not be cleaned (for example a
    missing name or an unparseable phone). Each item carries the raw source row and the
    reason it failed, so the quarantine bucket is actionable rather than an opaque count.
    """
    _summary, result, _matches = run_pipeline(database_url, returns_csv)
    return [
        {
            "source": r.source,
            "reason": r.reason,
        }
        for r in result.rejected
    ]


def propose_action(
    action: str,
    order_id: int,
    rationale: str,
    supporting_return_row: int | None = None,
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> dict:
    """
    Propose a write action for human confirmation. Validates but NEVER commits.

    The agent may propose confirm_order, hold_account, or issue_refund_note. This runs the
    full guardrail policy against the current reconciliation state and returns the verdict
    (allowed or not, with a reason). It writes nothing: an allowed proposal still requires a
    separate human confirm step to commit, and this tool has no path to that step.

    action must be one of: confirm_order, hold_account, issue_refund_note.
    supporting_return_row is required for issue_refund_note.
    """
    from guardrails.actions import propose
    from guardrails.models import ActionProposal, ActionType

    try:
        action_type = ActionType(action)
    except ValueError:
        return {
            "allowed": False,
            "reason": (
                f"Unknown action '{action}'. Must be one of: confirm_order, "
                "hold_account, issue_refund_note."
            ),
        }
    try:
        proposal = ActionProposal(
            action=action_type,
            order_id=order_id,
            rationale=rationale,
            supporting_return_row=supporting_return_row,
        )
    except ValidationError as exc:
        return {"allowed": False, "reason": f"Invalid proposal: {exc}"}
    _summary, result, matches = run_pipeline(database_url, returns_csv)
    verdict = propose(proposal, result.orders, result.returns, matches=matches)
    return {"allowed": verdict.allowed, "reason": verdict.reason}
