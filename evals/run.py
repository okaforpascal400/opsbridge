# evals/run.py
"""
The deterministic eval runner.

Runs every golden case, scores it pass or fail against its known-correct expected value,
and reports a results table. No LLM is involved, so this is exact, free, and reproducible:
the same seed produces the same score every time, which is what makes it a regression
guard worth running in CI.

Run it:
    python -m evals.run                 print the report, exit non-zero on a failed case
    python -m evals.run --write-readme  refresh the results table in README.md
    python -m evals.run --check         fail if that table has drifted from the real results

The README table is generated rather than hand-written, so it cannot quietly go stale: CI
runs --check, and a README claiming a score the suite no longer produces fails the build.
"""
from __future__ import annotations

import argparse
import difflib
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

README_PATH = Path(__file__).resolve().parent.parent / "README.md"
README_TABLE_START = "<!-- EVAL_TABLE_START -->"
README_TABLE_END = "<!-- EVAL_TABLE_END -->"


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


def _table_lines(results: list[EvalResult]) -> list[str]:
    lines = [
        "| id | category | result | expected | actual |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        mark = "pass" if r.passed else "FAIL"
        lines.append(
            f"| {r.case.id} | {r.case.category} | {mark} | "
            f"{r.case.expected} | {r.actual} |"
        )
    return lines


def format_report(results: list[EvalResult]) -> str:
    """Render the results as a markdown table plus a summary line."""
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    return "\n".join(
        ["# OpsBridge eval report", "", f"**{passed}/{total} passed**", ""]
        + _table_lines(results)
    )


def format_readme_block(results: list[EvalResult]) -> str:
    """Render the block that lives between the markers in README.md.

    No heading, because it sits inside a section the README already titles.
    """
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    return "\n".join(
        [
            f"**{passed}/{total} passing.** Generated by `python -m evals.run "
            "--write-readme`; CI fails if this table drifts from the real results.",
            "",
        ]
        + _table_lines(results)
    )


def _split_readme(readme_text: str, readme_path: Path) -> tuple[str, str, str]:
    """Return the text before the markers, the block between them, and the text after."""
    start = readme_text.find(README_TABLE_START)
    end = readme_text.find(README_TABLE_END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(
            f"{readme_path} is missing the {README_TABLE_START} / {README_TABLE_END} "
            "markers that bound the generated eval table."
        )
    return (
        readme_text[: start + len(README_TABLE_START)],
        readme_text[start + len(README_TABLE_START) : end].strip("\n"),
        readme_text[end:],
    )


def write_readme(results: list[EvalResult], readme_path: Path = README_PATH) -> None:
    """Replace the generated block in the README with the current results."""
    before, _old, after = _split_readme(
        readme_path.read_text(encoding="utf-8"), readme_path
    )
    readme_path.write_text(
        f"{before}\n{format_readme_block(results)}\n{after}",
        encoding="utf-8",
        newline="",
    )


def readme_drift(results: list[EvalResult], readme_path: Path = README_PATH) -> str:
    """Return a unified diff of README block versus current results, empty if they match."""
    _before, current, _after = _split_readme(
        readme_path.read_text(encoding="utf-8"), readme_path
    )
    expected = format_readme_block(results)
    if current == expected:
        return ""
    return "\n".join(
        difflib.unified_diff(
            current.splitlines(),
            expected.splitlines(),
            fromfile=f"{readme_path.name} (committed)",
            tofile="current results",
            lineterm="",
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic eval suite.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write-readme",
        action="store_true",
        help="write the results table into README.md between its markers",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="fail if README.md's table differs from the current results",
    )
    args = parser.parse_args()

    results = run_evals()
    failed = [r for r in results if not r.passed]

    if args.write_readme:
        write_readme(results)
        print(f"Wrote the results table into {README_PATH}.")
    elif args.check:
        drift = readme_drift(results)
        if drift:
            print("README.md's eval table is out of date. Run:")
            print("    python -m evals.run --write-readme\n")
            print(drift)
            raise SystemExit(1)
        print("README.md's eval table matches the current results.")
    else:
        print(format_report(results))

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
