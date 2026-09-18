# guardrails/policy.py
"""
Policy checks for proposed write actions.

This is the safety gate. validate_proposal takes an inert ActionProposal plus the current
reconciliation state and returns a PolicyResult: allowed or not, with a reason. It is pure
and deterministic, no I/O, so every rejection path is exhaustively testable without a
database, and the SAME function runs both at propose time (a courtesy filter so a human
never sees an impossible proposal) and at confirm time (the real gate, because state can
drift between the two, DECISION 028).

The rules (DECISION 004's actions, ROADMAP policy):
- every action:       the rationale must say something. The model requires a non-empty
                      string, which counts whitespace, so the gate is what enforces
                      "requires a reason": it is what a human reviews before confirming
                      and what the audit row records as the why.
- confirm_order:      the order must exist in the canonical orders.
- issue_refund_note:  a supporting return must exist AND have matched THIS order at HIGH
                      confidence. A LOW-confidence match is exactly the case the matcher
                      flagged as uncertain, so refunding on it would turn unresolved
                      uncertainty into a money movement; the policy refuses that.
- hold_account:       the order must exist; the rationale is the justification a human
                      reviews before the hold commits.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from guardrails.models import ActionProposal, ActionType
from schema_adapter.models import CanonicalOrder, MatchConfidence, ReturnMatch


class PolicyResult(BaseModel):
    """The verdict on a proposal: allowed or not, with a human-readable reason."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    reason: str = Field(min_length=1)


def _order_exists(order_id: int, orders: list[CanonicalOrder]) -> bool:
    return any(o.order_id == order_id for o in orders)


def _high_confidence_match(
    order_id: int, return_row: int, matches: list[ReturnMatch]
) -> bool:
    """True if the given return matched this order at HIGH confidence."""
    return any(
        m.return_row == return_row
        and m.matched_order_id == order_id
        and m.confidence == MatchConfidence.HIGH
        for m in matches
    )


def validate_proposal(
    proposal: ActionProposal,
    orders: list[CanonicalOrder],
    matches: list[ReturnMatch],
) -> PolicyResult:
    """Return whether a proposed action is allowed, with a reason. Pure; commits nothing."""
    if not proposal.rationale.strip():
        return PolicyResult(
            allowed=False,
            reason="A proposal requires a rationale; none was given.",
        )

    if not _order_exists(proposal.order_id, orders):
        return PolicyResult(
            allowed=False,
            reason=f"Order {proposal.order_id} does not exist in the canonical data.",
        )

    if proposal.action == ActionType.CONFIRM_ORDER:
        return PolicyResult(
            allowed=True, reason=f"Order {proposal.order_id} exists and may be confirmed."
        )

    if proposal.action == ActionType.HOLD_ACCOUNT:
        return PolicyResult(
            allowed=True,
            reason=(
                f"Order {proposal.order_id} exists and the proposal carries a rationale "
                "for a human to review."
            ),
        )

    if proposal.action == ActionType.ISSUE_REFUND_NOTE:
        if proposal.supporting_return_row is None:
            return PolicyResult(
                allowed=False,
                reason="A refund note requires a supporting return; none was provided.",
            )
        if not _high_confidence_match(
            proposal.order_id, proposal.supporting_return_row, matches
        ):
            return PolicyResult(
                allowed=False,
                reason=(
                    f"Return row {proposal.supporting_return_row} does not have a "
                    f"HIGH-confidence match to order {proposal.order_id}; resolve the "
                    "match before issuing a refund note."
                ),
            )
        return PolicyResult(
            allowed=True,
            reason=(
                f"Return row {proposal.supporting_return_row} matched order "
                f"{proposal.order_id} at high confidence; refund note is justified."
            ),
        )

    # unreachable: ActionType is a closed enum, but be explicit rather than silent
    return PolicyResult(allowed=False, reason=f"Unknown action: {proposal.action}.")
