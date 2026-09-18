"""OpsBridge HTTP API.

Exposes a health check and the human confirmation endpoint. The confirmation endpoint is
the HTTP surface of the guardrail write path: a client POSTs a proposed action, the server
re-validates it against current reconciliation state and, only if the policy allows, writes
one audit row through confirm(). The endpoint adds no new write path; it reuses the single
audited one in guardrails.actions, so every guardrail applies to HTTP callers too.

Run it with:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import create_engine

from config import get_settings
from guardrails.actions import confirm
from guardrails.models import ActionProposal, ActionType
from schema_adapter.pipeline import run_pipeline


class HealthResponse(BaseModel):
    """Body of a health check response."""

    status: str


class ConfirmRequest(BaseModel):
    """A human's confirmation of a proposed write action, posted as JSON."""

    action: ActionType
    order_id: int = Field(gt=0)
    rationale: str = Field(min_length=1)
    supporting_return_row: int | None = Field(default=None, ge=0)


class ConfirmResponse(BaseModel):
    """The verdict of a confirmation attempt."""

    allowed: bool
    reason: str


app: FastAPI = FastAPI(title="OpsBridge API")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report that the process is up and serving requests."""
    return HealthResponse(status="ok")


def _confirm_proposal(
    proposal: ActionProposal,
    database_url: str | None = None,
    returns_csv: Path | None = None,
) -> ConfirmResponse:
    """Re-validate against current state and write through the audited confirm() path.

    Kept separate from the route so it can be tested against a throwaway database. It
    fetches confirm-time state itself (DECISION 028): the client sends only the proposal,
    the server validates against live reconciliation data, not a snapshot the client held.
    """
    url = database_url or get_settings().database_url
    _summary, result, matches = run_pipeline(database_url, returns_csv)
    engine = create_engine(url)
    try:
        verdict = confirm(
            proposal, engine, result.orders, result.returns, matches=matches
        )
    finally:
        engine.dispose()
    return ConfirmResponse(allowed=verdict.allowed, reason=verdict.reason)


@app.post("/confirm", response_model=ConfirmResponse)
def confirm_action(request: ConfirmRequest) -> ConfirmResponse:
    """Confirm a proposed write action over HTTP.

    The request body is validated by FastAPI (a malformed body is a 422). A well-formed
    request is turned into a proposal and run through the guardrail confirm path; a refused
    proposal returns 200 with allowed=false, because a refusal is a valid outcome, not a
    server error. Only an allowed proposal writes one audit row.
    """
    proposal = ActionProposal(
        action=request.action,
        order_id=request.order_id,
        rationale=request.rationale,
        supporting_return_row=request.supporting_return_row,
    )
    return _confirm_proposal(proposal)
