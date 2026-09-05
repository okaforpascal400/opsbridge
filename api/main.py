"""OpsBridge HTTP API.

Phase 1 exposes a health check and nothing else. The query, proposal and trace
endpoints arrive in later phases; keeping this module thin now means the app can
be deployed and monitored before there is anything interesting behind it.

Run it with:
    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Body of a health check response."""

    status: str


app: FastAPI = FastAPI(title="OpsBridge API")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report that the process is up and serving requests."""
    return HealthResponse(status="ok")
