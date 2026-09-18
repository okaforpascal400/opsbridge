# tests/test_guardrail_models.py
"""
Tests for the ActionProposal contract.

These pin the SHAPE and the field constraints of a proposal, not the policy rules (those
live in test_policy.py). The proposal is inert data: it must carry an action, a target
order, and a non-empty rationale, and it must reject malformed input at construction so a
bad proposal cannot exist in the first place.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from guardrails.models import ActionProposal, ActionType


def test_a_valid_confirm_proposal_builds():
    p = ActionProposal(
        action=ActionType.CONFIRM_ORDER,
        order_id=42,
        rationale="Return matched this order at high confidence.",
    )
    assert p.action == ActionType.CONFIRM_ORDER
    assert p.order_id == 42
    assert p.supporting_return_row is None


def test_action_type_values():
    # the three actions from DECISION 004, by their wire values
    assert ActionType.CONFIRM_ORDER == "confirm_order"
    assert ActionType.HOLD_ACCOUNT == "hold_account"
    assert ActionType.ISSUE_REFUND_NOTE == "issue_refund_note"


def test_refund_note_can_carry_a_supporting_return():
    p = ActionProposal(
        action=ActionType.ISSUE_REFUND_NOTE,
        order_id=7,
        rationale="Return row 3 matched order 7; issuing the refund note.",
        supporting_return_row=3,
    )
    assert p.supporting_return_row == 3


def test_rationale_is_required_and_non_empty():
    with pytest.raises(ValidationError):
        ActionProposal(action=ActionType.HOLD_ACCOUNT, order_id=1, rationale="")


def test_order_id_must_be_positive():
    with pytest.raises(ValidationError):
        ActionProposal(action=ActionType.CONFIRM_ORDER, order_id=0, rationale="x")


def test_unknown_action_is_rejected():
    with pytest.raises(ValidationError):
        ActionProposal(action="delete_everything", order_id=1, rationale="x")


def test_extra_fields_are_forbidden():
    # a proposal must not carry unexpected fields; the contract is closed
    with pytest.raises(ValidationError):
        ActionProposal(
            action=ActionType.CONFIRM_ORDER,
            order_id=1,
            rationale="x",
            secret_backdoor=True,
        )
