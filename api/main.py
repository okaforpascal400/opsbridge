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
from typing import Annotated

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine

from config import get_settings
from guardrails.actions import confirm
from guardrails.models import ActionProposal, ActionType
from schema_adapter.pipeline import run_pipeline


class HealthResponse(BaseModel):
    """Body of a health check response."""

    status: str


class ConfirmRequest(BaseModel):
    """A human's confirmation of a proposed write action, posted as JSON.

    Extra fields are refused rather than ignored, so a client that sends something the
    server does not act on (confirmed_by, say) gets a 422 naming it instead of a 200 that
    quietly drops it. The rationale is capped because it lands in an append-only table
    that nothing deletes from.
    """

    model_config = ConfigDict(extra="forbid")

    action: ActionType
    order_id: int = Field(gt=0)
    rationale: str = Field(min_length=1, max_length=2000)
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


def get_database_url() -> str:
    """The database the confirm path reads and writes.

    A FastAPI dependency so a test can override it and drive the route against a throwaway
    database instead of the configured one.
    """
    return get_settings().database_url


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
    _summary, result, matches = run_pipeline(url, returns_csv)
    # one engine per call: a confirmation is human-triggered, so the connection setup is
    # not worth pooling unless this ever becomes a hot path
    engine = create_engine(url)
    try:
        verdict = confirm(
            proposal, engine, result.orders, result.returns, matches=matches
        )
    finally:
        engine.dispose()
    return ConfirmResponse(allowed=verdict.allowed, reason=verdict.reason)


@app.post("/confirm", response_model=ConfirmResponse)
def confirm_action(
    request: ConfirmRequest,
    database_url: Annotated[str, Depends(get_database_url)],
) -> ConfirmResponse:
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
    return _confirm_proposal(proposal, database_url=database_url)
