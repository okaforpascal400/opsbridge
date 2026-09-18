# evals/run.py
"""
The deterministic eval runner.

Runs every golden case, scores it pass or fail against its known-correct expected value,
and reports a results table. No LLM is involved, so this is exact, free, and reproducible:
the same seed produces the same score every time, which is what makes it a regression
guard worth running in CI.

Run it:
    python -m evals.run
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from evals.golden import GOLDEN_CASES, EvalCase
from guardrails.models import ActionProposal, ActionType
from guardrails.policy import validate_proposal
from schema_adapter.models import (
    CanonicalOrder,
    CanonicalReturn,
    MatchConfidence,
    OrderStatus,
    ReturnMatch,
)
from schema_adapter.pipeline import run_pipeline


@dataclass
class EvalResult:
    case: EvalCase
    passed: bool
    actual: object


_STATUS_BY_NAME = {
    "pending": OrderStatus.PENDING,
    "unknown": OrderStatus.UNKNOWN,
    "delivered": OrderStatus.DELIVERED,
    "returned": OrderStatus.RETURNED,
    "confirmed": OrderStatus.CONFIRMED,
}
_CONFIDENCE_BY_NAME = {
    "high": MatchConfidence.HIGH,
    "low": MatchConfidence.LOW,
}


def _order(order_id: int, status: OrderStatus, amount: str = "162500") -> CanonicalOrder:
    return CanonicalOrder(
        order_id=order_id,
        customer_name="Test Customer",
        order_date=date(2026, 6, 15),
        status=status,
        phone="+2348031234567",
        amount=Decimal(amount),
    )


def _run_case(
    case: EvalCase,
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> EvalResult:
    """Execute one case and compare its actual outcome to the expected value."""
    if case.check == "summary_field":
        summary, _r, _m = run_pipeline(database_url, returns_csv)
        actual = getattr(summary, case.params["field"])

    elif case.check == "summary_match_tier":
        summary, _r, _m = run_pipeline(database_url, returns_csv)
        tier = case.params["tier"]
        actual = {
            "high_auto_accept": summary.matched_high,
            "low_needs_review": summary.matched_low,
            "none_no_match": summary.matched_none,
        }[tier]

    elif case.check == "confirm_status_allowed":
        status = _STATUS_BY_NAME[case.params["status"]]
        orders = [_order(1, status)]
        proposal = ActionProposal(
            action=ActionType.CONFIRM_ORDER, order_id=1, rationale="eval"
        )
        actual = validate_proposal(proposal, orders).allowed

    elif case.check == "refund_case":
        confidence = _CONFIDENCE_BY_NAME[case.params["confidence"]]
        orders = [_order(1, OrderStatus.DELIVERED, amount=case.params["order_amount"])]
        returns = [
            CanonicalReturn(
                return_row=3,
                customer_name="Test Customer",
                phone="+2348031234567",
                amount=Decimal(case.params["return_amount"]),
                reason="eval",
            )
        ]
        matches = [
            ReturnMatch(
                return_row=3,
                matched_order_id=1,
                confidence=confidence,
                score=0.95 if confidence == MatchConfidence.HIGH else 0.6,
                rationale="eval",
            )
        ]
        proposal = ActionProposal(
            action=ActionType.ISSUE_REFUND_NOTE,
            order_id=1,
            rationale="eval",
            supporting_return_row=3,
        )
        actual = validate_proposal(proposal, orders, returns, matches=matches).allowed

    else:
        raise ValueError(f"unknown check: {case.check}")

    return EvalResult(case=case, passed=actual == case.expected, actual=actual)


def run_evals(
    database_url: str | None = None, returns_csv: Path | None = None
) -> list[EvalResult]:
    """Run every golden case and return the results."""
    return [_run_case(case, database_url, returns_csv) for case in GOLDEN_CASES]


def format_report(results: list[EvalResult]) -> str:
    """Render the results as a markdown table plus a summary line."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines = [
        "# OpsBridge eval report",
        "",
        f"**{passed}/{total} passed**",
        "",
        "| id | category | result | expected | actual |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        mark = "pass" if r.passed else "FAIL"
        lines.append(
            f"| {r.case.id} | {r.case.category} | {mark} | "
            f"{r.case.expected} | {r.actual} |"
        )
    return "\n".join(lines)


def main() -> None:
    results = run_evals()
    print(format_report(results))
    failed = [r for r in results if not r.passed]
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
