# evals/golden.py
"""
The golden dataset: known inputs with known-correct expected outcomes.

Every expected value here was read from the real seeded pipeline, not guessed, so a case
that fails means the system's behaviour changed, not that the dataset is wrong. The cases
are deterministic: they exercise the tools, the policy gate, and the matcher directly,
with no LLM in the loop, so this suite is exact, free, and safe to run in CI as a
regression guard. The live agent eval (evals/live.py) reuses this dataset's questions to
measure the end-to-end agent separately.

Ground truth (from run_pipeline against the committed seed):
  orders_in=200, all canonicalized, none quarantined
  returns_in=40, all canonicalized, all matched HIGH (0 low, 0 none)
  canonical order statuses: CONFIRMED 84, DELIVERED 44, PENDING 52, RETURNED 10, UNKNOWN 10
  confirmable (PENDING or UNKNOWN) = 62; refused = 138
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EvalCategory(StrEnum):
    PIPELINE = "pipeline"
    POLICY = "policy"
    MATCHER = "matcher"


class EvalCase(BaseModel):
    """One golden case: an id, what it checks, and the expected outcome."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: EvalCategory
    description: str
    # the kind of check and its parameters; interpreted by the runner
    check: str
    params: dict = Field(default_factory=dict)
    expected: object


GOLDEN_CASES: list[EvalCase] = [
    # --- pipeline reconciliation counts -------------------------------------------
    EvalCase(
        id="pipe-orders-in",
        category=EvalCategory.PIPELINE,
        description="All 200 seeded orders are read in.",
        check="summary_field",
        params={"field": "orders_in"},
        expected=200,
    ),
    EvalCase(
        id="pipe-orders-canonicalized",
        category=EvalCategory.PIPELINE,
        description="Every order canonicalizes; none is quarantined.",
        check="summary_field",
        params={"field": "orders_canonicalized"},
        expected=200,
    ),
    EvalCase(
        id="pipe-orders-quarantined",
        category=EvalCategory.PIPELINE,
        description="No order is quarantined on the clean seed.",
        check="summary_field",
        params={"field": "orders_quarantined"},
        expected=0,
    ),
    EvalCase(
        id="pipe-returns-in",
        category=EvalCategory.PIPELINE,
        description="All 40 seeded returns are read in.",
        check="summary_field",
        params={"field": "returns_in"},
        expected=40,
    ),
    EvalCase(
        id="pipe-matches-high",
        category=EvalCategory.MATCHER,
        description="Every return matches its order at HIGH confidence.",
        check="summary_match_tier",
        params={"tier": "high_auto_accept"},
        expected=40,
    ),
    EvalCase(
        id="pipe-matches-low",
        category=EvalCategory.MATCHER,
        description="No return needs review on the recoverable seed.",
        check="summary_match_tier",
        params={"tier": "low_needs_review"},
        expected=0,
    ),
    EvalCase(
        id="pipe-matches-none",
        category=EvalCategory.MATCHER,
        description="No return is unmatched on the recoverable seed.",
        check="summary_match_tier",
        params={"tier": "none_no_match"},
        expected=0,
    ),
    # --- policy: confirm depends on order status ----------------------------------
    EvalCase(
        id="policy-confirm-pending-allowed",
        category=EvalCategory.POLICY,
        description="A pending order may be confirmed.",
        check="confirm_status_allowed",
        params={"status": "pending"},
        expected=True,
    ),
    EvalCase(
        id="policy-confirm-unknown-allowed",
        category=EvalCategory.POLICY,
        description="An unknown-status order may be confirmed.",
        check="confirm_status_allowed",
        params={"status": "unknown"},
        expected=True,
    ),
    EvalCase(
        id="policy-confirm-delivered-refused",
        category=EvalCategory.POLICY,
        description="A delivered order cannot be confirmed.",
        check="confirm_status_allowed",
        params={"status": "delivered"},
        expected=False,
    ),
    EvalCase(
        id="policy-confirm-returned-refused",
        category=EvalCategory.POLICY,
        description="A returned order cannot be confirmed.",
        check="confirm_status_allowed",
        params={"status": "returned"},
        expected=False,
    ),
    EvalCase(
        id="policy-confirm-confirmed-refused",
        category=EvalCategory.POLICY,
        description="An already-confirmed order cannot be re-confirmed.",
        check="confirm_status_allowed",
        params={"status": "confirmed"},
        expected=False,
    ),
    # --- policy: refund safety ----------------------------------------------------
    EvalCase(
        id="policy-refund-high-match-allowed",
        category=EvalCategory.POLICY,
        description="A refund backed by a HIGH-confidence match within the order amount is allowed.",
        check="refund_case",
        params={"confidence": "high", "return_amount": "50000", "order_amount": "162500"},
        expected=True,
    ),
    EvalCase(
        id="policy-refund-low-match-refused",
        category=EvalCategory.POLICY,
        description="A refund backed only by a LOW-confidence match is refused.",
        check="refund_case",
        params={"confidence": "low", "return_amount": "50000", "order_amount": "162500"},
        expected=False,
    ),
    EvalCase(
        id="policy-refund-over-amount-refused",
        category=EvalCategory.POLICY,
        description="A refund exceeding the order amount is refused even with a HIGH match.",
        check="refund_case",
        params={"confidence": "high", "return_amount": "999999", "order_amount": "162500"},
        expected=False,
    ),
]
