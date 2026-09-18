# guardrails/models.py
"""
The proposal contract for human-in-the-loop write actions.

The agent may PROPOSE a write action but never execute one (DECISION 005). An
ActionProposal is inert: creating it commits nothing. It carries what a human reviewer
needs to make one clear decision, the action, the record it affects, why it is proposed,
and any supporting reference, plus enough for the confirm step to re-validate and record
the action in the audit table. Execution happens only on confirmation, in a separate step.

Validation lives in guardrails/policy.py, not here: this module is the shape of a
proposal, not the rules for whether one is allowed.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    """The three write actions the agent may propose (DECISION 004)."""

    CONFIRM_ORDER = "confirm_order"
    HOLD_ACCOUNT = "hold_account"
    ISSUE_REFUND_NOTE = "issue_refund_note"


class ActionProposal(BaseModel):
    """A proposed write action awaiting human confirmation. Inert until confirmed."""

    model_config = ConfigDict(extra="forbid")

    action: ActionType
    order_id: int = Field(gt=0, description="the order the action targets")
    rationale: str = Field(
        min_length=1, description="why the action is proposed; a human reads this"
    )
    supporting_return_row: int | None = Field(
        default=None,
        ge=0,
        description="for a refund note, the matched return that justifies it",
    )
