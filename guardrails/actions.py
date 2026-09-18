# guardrails/actions.py
"""
The confirm flow: the only path that commits a write action.

The agent proposes; a human confirms; only confirmation writes. This module enforces that
as code. propose() runs the policy and returns the verdict without touching the database.
confirm() re-runs the SAME policy (confirm-time validation, DECISION 028, because state can
drift between propose and confirm) and writes an audit row to opsbridge.actions ONLY when
the policy allows it. There is no other function that writes to the actions table, so no
action can be recorded without passing confirmation.

The actions table lives in a new opsbridge schema (DECISION 027): the legacy schema is
never touched, the table is created with IF NOT EXISTS, and nothing here updates or deletes
a row, so the trail only grows. Append-only is a property of this code, not of the
database: the application role still holds UPDATE and DELETE, so revoking them is a
deployment step, not something this module can claim. The row records what was confirmed
and why, but not who confirmed it: there is no confirmation endpoint or operator identity
yet, so confirmed_by and trace_id land with the endpoint rather than as placeholders.
"""
from __future__ import annotations

from typing import Final

from psycopg2 import errors as pg_errors
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from guardrails.models import ActionProposal, ActionType
from guardrails.policy import PolicyResult, validate_proposal
from schema_adapter.models import CanonicalOrder, CanonicalReturn, ReturnMatch

ACTIONS_SCHEMA: Final[str] = "opsbridge"
ACTIONS_TABLE: Final[str] = "actions"

_CREATE_SCHEMA_SQL: Final[str] = f"CREATE SCHEMA IF NOT EXISTS {ACTIONS_SCHEMA}"
_CREATE_ACTIONS_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {ACTIONS_SCHEMA}.{ACTIONS_TABLE} (
    action_id             SERIAL PRIMARY KEY,
    action                TEXT NOT NULL,
    order_id              INTEGER NOT NULL,
    supporting_return_row INTEGER NULL,
    rationale             TEXT NOT NULL,
    committed_at          TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_INSERT_ACTION_SQL: Final[str] = f"""
INSERT INTO {ACTIONS_SCHEMA}.{ACTIONS_TABLE}
    (action, order_id, supporting_return_row, rationale)
VALUES
    (:action, :order_id, :supporting_return_row, :rationale)
"""


# Postgres CREATE ... IF NOT EXISTS checks the catalogue without serialising, so two
# sessions creating the schema or table at the same time both pass the check and one loses
# on the catalogue's unique index. Losing that race means the object now exists, which is
# the outcome we wanted, so it is success rather than failure.
_RACE_LOST_ERRORS: Final = (
    pg_errors.UniqueViolation,
    pg_errors.DuplicateSchema,
    pg_errors.DuplicateTable,
    pg_errors.DuplicateObject,
)


def _execute_ignoring_lost_race(engine: Engine, statement: str) -> None:
    """Run one DDL statement, treating "someone else created it first" as success."""
    try:
        with engine.begin() as conn:
            conn.execute(text(statement))
    except DBAPIError as exc:
        if not isinstance(exc.orig, _RACE_LOST_ERRORS):
            raise


def ensure_actions_table(engine: Engine) -> None:
    """Create the opsbridge schema and actions table if they do not exist.

    Idempotent and non-destructive: unlike the seed, this never drops, because the table
    is an audit trail. Each statement runs in its own transaction, because a lost creation
    race aborts the transaction it happened in and the next statement could not run.
    """
    _execute_ignoring_lost_race(engine, _CREATE_SCHEMA_SQL)
    _execute_ignoring_lost_race(engine, _CREATE_ACTIONS_SQL)


def propose(
    proposal: ActionProposal,
    orders: list[CanonicalOrder],
    returns: list[CanonicalReturn] | None = None,
    *,
    matches: list[ReturnMatch] | None = None,
) -> PolicyResult:
    """Validate a proposed action without committing anything (propose-time check).

    Returns the policy verdict. A caller shows an allowed proposal to a human for
    confirmation; a rejected one never reaches a human.
    """
    return validate_proposal(proposal, orders, returns, matches=matches)


def confirm(
    proposal: ActionProposal,
    engine: Engine,
    orders: list[CanonicalOrder],
    returns: list[CanonicalReturn] | None = None,
    *,
    matches: list[ReturnMatch] | None = None,
) -> PolicyResult:
    """Re-validate a proposal and, only if it passes, write it to the audit table.

    This is the sole write path. It re-runs the policy next to the write rather than
    trusting the earlier propose(), so the caller must hand it state read at confirm time,
    not the snapshot propose() saw: this function validates what it is given and does not
    re-read the sources itself. If the policy rejects, the table is untouched and the
    rejection is returned. If it allows, one audit row is written and the allow verdict is
    returned.

    A refund note additionally requires the cited return to be supplied here. The policy
    skips the amount ceiling when it cannot find that return, which is a harmless gap for
    an inert verdict but not next to a write: it would let a refund above the order amount
    reach the audit table.
    """
    result = validate_proposal(proposal, orders, returns, matches=matches)
    if not result.allowed:
        return result

    if proposal.action == ActionType.ISSUE_REFUND_NOTE and not any(
        r.return_row == proposal.supporting_return_row for r in (returns or [])
    ):
        return PolicyResult(
            allowed=False,
            reason=(
                f"Return row {proposal.supporting_return_row} was not supplied to the "
                f"confirm step, so its amount could not be checked against order "
                f"{proposal.order_id}; pass the canonical returns to confirm."
            ),
        )

    ensure_actions_table(engine)
    with engine.begin() as conn:
        conn.execute(
            text(_INSERT_ACTION_SQL),
            {
                "action": str(proposal.action),
                "order_id": proposal.order_id,
                "supporting_return_row": proposal.supporting_return_row,
                "rationale": proposal.rationale,
            },
        )
    return result


def count_actions(engine: Engine) -> int:
    """Return how many actions have been recorded. Used by tests and observability.

    A read, and only a read: it does not create the table, so a missing audit table raises
    here rather than being silently recreated and reported as zero actions.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(f"SELECT count(*) FROM {ACTIONS_SCHEMA}.{ACTIONS_TABLE}")
        )
        return int(result.scalar_one())
