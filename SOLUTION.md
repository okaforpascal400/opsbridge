# OpsBridge: solution write-up

This is the write-up an engineer produces after sitting with a team, understanding how the
work actually happens, and designing the system that changes it. It states the problem, the
approach, the decisions that carried real trade-offs, what was deliberately left out, and how
the result is measured.

## The problem

Every operations team runs on data that lives in more than one place and agrees with itself
in none of them. A typical picture, and the one this project is built around:

- Orders live in a database whose columns drifted over years of half-migrations. The customer
  name is in one of two columns depending on the row. Dates are text in three different
  formats. Amounts are sometimes "N162,500" and sometimes 162500. Statuses are free text a
  person typed on the phone: "confirmed", "CONF", "pending call", and blank.
- Returns arrive as a spreadsheet export from a different tool. It carries no order id. The
  only way back to the order is the customer name and phone, and both are lightly wrong: names
  are misspelled, phones are written in a different format than the order system uses.

An analyst spends the first hour of the day reconciling these by hand: matching a return to
its order, deciding whether a refund is justified, confirming orders that are still open. It
is slow, it does not scale, and it is exactly the kind of judgement work a team would like to
give to software. But nobody will let software touch it, because a wrong refund is real money
leaving the business, and a silent mistake in reconciliation is worse than a slow human.

So the real problem is not "can AI read this data". It is "can AI be trusted to act on it".
That trust has to be built, not assumed.

## The approach

Reconcile first, then let an agent answer questions and propose actions under human control,
and measure the whole thing.

1. Make the data trustworthy before anything intelligent touches it. A reconciliation layer
   turns every messy source row into a clean, validated record or quarantines it with a
   reason. This layer is deterministic and fully tested; it contains no AI. The intelligence
   later sits on top of trustworthy data, never on top of a guess.

2. Match returns to orders honestly. Because the phone is reliable after normalisation and the
   name is deliberately noisy, the matcher weights phone heavily and uses the name to confirm.
   Crucially, when the evidence is only partial it does not guess: it flags the match for a
   human as LOW confidence. A silent wrong match is worse than an admitted "I am not sure".

3. Put an agent on top that answers natural-language questions by calling the reconciliation
   layer as tools. The agent has no special knowledge; it orchestrates tested functions and
   states only facts those functions return.

4. Let the agent propose write-actions, never execute them. It can propose confirming an order,
   holding an account, or issuing a refund note. A proposal is inert. A separate human
   confirmation step re-validates the proposal against current state and is the only path that
   writes anything. The agent structurally cannot act on its own.

5. Record what the system did and why. Every agent turn is traced, and every confirmed action
   is written to an append-only audit table, so a confirmation can be traced back to the
   reasoning that produced it.

6. Measure it. A deterministic eval suite gates every change in CI, and a live suite measures
   the whole agent against the real model on demand.

## Decisions that carried a trade-off

The full log is in DECISIONS.md. The ones that most define the system:

- The agent proposes, a human confirms, and confirmation is the sole write path. This is what
  makes "trusted to act" real rather than aspirational. The agent cannot execute a write; there
  is no code path from the model to the database that skips the human.

- Money actions fail closed. A refund is refused unless a matched return backs it at HIGH
  confidence and its amount does not exceed the order. When the confirm step cannot verify the
  amount because the return was not supplied, it refuses rather than proceeds. The last gate
  before money moves refuses what it cannot check.

- The legacy schema is never mutated. Actions are recorded in a separate audit schema, because
  a real integration rarely has permission to alter the source system and must not risk its
  integrity. The audit trail is append-only.

- Observability never changes behaviour. Tracing is optional and additive: the agent returns
  the same answer with or without it. A crash is recorded then re-raised so it is both visible
  and still loud; a trace save failure is swallowed so it can never break a turn.

- Correctness is enforced, not asserted. The eval suite reads its expected values from the real
  data and fails the build on any drift. The README's own results table is generated and
  CI-checked, so it cannot silently go stale.

## What is deliberately deferred

Naming the edges is part of the design, not an omission.

- Retrieval over the invoice documents (RAG) is a separate build and is deferred to its own
  phase; it needs a document store and embeddings.
- Authentication. The confirmation endpoint is unauthenticated, so attribution is currently
  caller-asserted. Operator identity (confirmed_by) and a real auth layer are the next
  hardening step, and the trace_id correlation is already in place to hang identity off.
- A stdout/aggregator logger. Failures are captured to the trace store but not yet shipped to a
  log pipeline; a swallowed save failure is currently silent until that logger exists.

## How it is measured

Two tiers. A deterministic suite of golden cases (pipeline counts, matcher tiers, guardrail
policy) runs in CI on every push and fails the build on any regression; its expected values are
read from the real seed, so a failure means behaviour changed. A live suite runs real questions
through the agent against the model on demand. The gate has already earned its place: flipping a
single comparison in the refund policy broke two eval cases at once, caught immediately.

The point of the whole system is the last mile: not that AI can read messy data, but that it can
be trusted to act on it, with the guardrails, the audit trail, and the measurement that trust
requires.
