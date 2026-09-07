# schema_adapter/models.py
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class OrderStatus(StrEnum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    DELIVERED = "delivered"
    RETURNED = "returned"
    UNKNOWN = "unknown"


class CanonicalOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int = Field(gt=0)
    customer_name: str = Field(min_length=1)
    order_date: date
    status: OrderStatus
    phone: str = Field(min_length=7)
    amount: Decimal = Field(ge=0)


class CanonicalReturn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_row: int = Field(ge=0)
    customer_name: str = Field(min_length=1)
    phone: str = Field(min_length=7)
    amount: Decimal = Field(ge=0)
    reason: str = Field(default="")  # optional per DECISION 013, may be empty


class MatchConfidence(StrEnum):
    HIGH = "high"
    LOW = "low"
    NONE = "none"


class ReturnMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_row: int = Field(ge=0)
    matched_order_id: int | None = Field(default=None, gt=0)
    confidence: MatchConfidence
    score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)


class RejectedRow(BaseModel):
    """A source row that could not be canonicalized. Quarantine per DECISION 012."""
    model_config = ConfigDict(extra="forbid")
    source: dict = Field(description="the raw source row, verbatim")
    reason: str = Field(min_length=1, description="why it failed canonicalization")


class ReconciliationResult(BaseModel):
    """The full output: what canonicalized cleanly, and what didn't."""
    model_config = ConfigDict(extra="forbid")
    orders: list[CanonicalOrder] = Field(default_factory=list)
    returns: list[CanonicalReturn] = Field(default_factory=list)
    rejected: list[RejectedRow] = Field(default_factory=list)