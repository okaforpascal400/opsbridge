"""OpsBridge HTTP API.

Exposes a health check, the human confirmation endpoint, a trace reader, two routes for
the demo UI (a reconciliation summary and a question box for the agent), and the console
page itself at the root path, the only route here that serves HTML rather than JSON.
The trace reader answers the debugging question: what did the agent do on a given turn,
in order, with what inputs, outputs, latencies and errors. The confirmation endpoint is
the HTTP surface of the guardrail write path: a client POSTs a proposed action, the server
re-validates it against current reconciliation state and, only if the policy allows, writes
one audit row through confirm(). The endpoint adds no new write path; it reuses the single
audited one in guardrails.actions, so every guardrail applies to HTTP callers too.

Run it with:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine

from agent.conversation import Conversation
from config import get_settings
from guardrails.actions import confirm
from guardrails.models import ActionProposal, ActionType
from observability.trace import load_trace
from schema_adapter.pipeline import run_pipeline

# Resolved from this module's location, not the working directory, so the console is
# served whether the app was started from the repo root, from a container, or by a
# process manager with a working directory of its own.
INDEX_HTML = Path(__file__).parent / "static" / "index.html"


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
    trace_id: str | None = Field(
        default=None,
        max_length=64,
        description="the agent turn this confirmation came from, if there was one",
    )


class ConfirmResponse(BaseModel):
    """The verdict of a confirmation attempt."""

    allowed: bool
    reason: str


class TraceStepResponse(BaseModel):
    """One step of a recorded turn: a model call or a tool call."""

    kind: str
    name: str
    input: Any = None
    output: Any = None
    latency_ms: int
    error: str | None = None


class TraceResponse(BaseModel):
    """The timeline of one recorded turn, oldest step first."""

    trace_id: str
    question: str
    final_answer: str
    total_ms: int
    created_at: str
    steps: list[TraceStepResponse]


class SummaryResponse(BaseModel):
    """The aggregate reconciliation picture, as the demo UI shows it."""

    orders_in: int
    orders_canonicalized: int
    orders_quarantined: int
    returns_in: int
    returns_canonicalized: int
    returns_quarantined: int
    matched_high: int
    matched_low: int
    matched_none: int


class AskRequest(BaseModel):
    """One operations question for the agent, posted as JSON.

    The question is capped because every character is sent to the model and stored in the
    trace. Extra fields are refused, as on /confirm, so a client never gets a 200 for
    something the server ignored.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)


class AskResponse(BaseModel):
    """The agent's answer and the id of the trace that records how it got there."""

    answer: str
    trace_id: str


app: FastAPI = FastAPI(title="OpsBridge API")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report that the process is up and serving requests."""
    return HealthResponse(status="ok")


def get_database_url() -> str:
    """The database the routes read and write.

    A FastAPI dependency so a test can override it and drive a route against a throwaway
    database instead of the configured one.
    """
    return get_settings().database_url


def _confirm_proposal(
    proposal: ActionProposal,
    database_url: str | None = None,
    returns_csv: Path | None = None,
    trace_id: str | None = None,
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
            proposal,
            engine,
            result.orders,
            result.returns,
            matches=matches,
            trace_id=trace_id,
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
    return _confirm_proposal(
        proposal, database_url=database_url, trace_id=request.trace_id
    )


@app.get("/trace/{trace_id}", response_model=TraceResponse)
def read_trace(
    trace_id: str,
    database_url: Annotated[str, Depends(get_database_url)],
) -> TraceResponse:
    """Return the recorded timeline of one agent turn: why it answered what it answered.

    A plain read. An unknown id is a 404 rather than an empty timeline, because "no such
    trace" and "a trace with no steps" are different answers to the debugging question.
    """
    engine = create_engine(database_url)
    try:
        trace = load_trace(trace_id, engine)
    finally:
        engine.dispose()

    if trace is None:
        raise HTTPException(status_code=404, detail=f"No trace with id {trace_id}.")
    return TraceResponse(**trace)


@app.get("/summary", response_model=SummaryResponse)
def read_summary(
    database_url: Annotated[str, Depends(get_database_url)],
) -> SummaryResponse:
    """Return the reconciliation summary: rows in, canonicalized, quarantined, matched.

    A plain read. It runs the deterministic pipeline and nothing else: no model call and
    no write, so it is safe to call on every page load.
    """
    summary, _result, _matches = run_pipeline(database_url)
    return SummaryResponse(**summary.model_dump())


@app.post("/ask", response_model=AskResponse)
def ask_question(
    request: AskRequest,
    database_url: Annotated[str, Depends(get_database_url)],
) -> AskResponse:
    """Put one question to the agent and return its answer with the turn's trace id.

    This calls the real model, so CI never exercises it; the tests stub Conversation. Each
    request starts a fresh Conversation, so there is no follow-up context across requests.
    The trace is saved by Conversation itself, and the returned id reads back through
    GET /trace/{trace_id}. It is an empty string only if no trace id was assigned.
    """
    # the route owns the engine the trace is saved through: left to Conversation, each
    # request would build one that is never disposed, and its pooled connection would
    # stay open until the garbage collector happened to run
    engine = create_engine(database_url)
    try:
        convo = Conversation(engine=engine)
        answer = convo.ask(request.question)
    finally:
        engine.dispose()
    return AskResponse(answer=answer, trace_id=convo.last_trace_id or "")


@app.get("/", response_class=FileResponse, include_in_schema=False)
def console() -> FileResponse:
    """Serve the demo console, a single self-contained page that calls the routes above.

    The page holds no logic of its own: it reads /summary, posts questions to /ask, posts
    proposals to /confirm and reads /trace/{id}, so what it shows is what the API returns.
    It is left out of the OpenAPI schema because it is a page, not an API operation, and
    the generated docs should list the JSON surface only.
    """
    return FileResponse(INDEX_HTML)
