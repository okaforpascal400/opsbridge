# OpsBridge in plain English: a study companion

This file is for me. It explains what the project does and why, in language I can say out
loud to an engineer or to a non-technical person, so I can defend any part of it cold. Each
section has the idea, why it is built that way, and the one-line answer for when someone
probes it.

## The one-sentence version

OpsBridge takes a messy pile of business data that lives in several places and does not agree
with itself, cleans it into something trustworthy, lets an AI agent answer questions about it,
and lets that agent propose actions that a human must approve before anything happens. Every
step is measured and recorded.

## Why this project exists

The interesting problem in AI right now is not "can the model read the data". Models can do
that. The problem is "can the AI be trusted to act on the data". A wrong refund is real money.
So the whole project is about earning that trust with engineering, not assuming it.

The role this targets, Forward Deployed Engineer, is the person who takes a real business mess
and turns it into working software that carries responsibility. Three skills in one: building
software, building with AI, and architecting the whole solution. This project is evidence of
all three.

## The layers, bottom to top

### 1. The messy data (the seed)

I built a fake but realistic mess on purpose: an orders database with inconsistent columns,
three date formats, amounts written two ways, free-text statuses, and a returns spreadsheet
with no shared key back to the orders, just a misspelled name and a reformatted phone.

Why fake it: the FDE job is integrating into someone else's mess. A clean dataset would remove
the skill being shown. The mess is the point.
One-liner: "I generate a deliberately messy multi-source stack because the real skill is
reconciling data that does not agree with itself, not processing clean data."

### 2. The reconciliation layer (normalizers, matcher)

Small functions each fix one kind of mess: one parses the three date formats, one folds the
three phone formats into a single canonical form, one turns messy statuses into a fixed set,
one parses money into an exact type. Then a matcher links each return to its order.

Two ideas worth defending:
- Money is stored as Decimal, never a floating-point number, because floats cannot represent
  currency exactly. One-liner: "I use Decimal for money because float rounding corrupts
  currency."
- I resolved an ambiguous date format (07/03/2026 could be March or July) by reading the code
  that generated the data rather than guessing. One-liner: "When data was ambiguous I read the
  source that produced it, because guessing month-first would silently corrupt every ambiguous
  date while still looking like it worked."

### 3. The fuzzy matcher (the interview centrepiece)

Matching a return to an order is not exact: the name is misspelled and the phone is
reformatted. So the matcher scores how likely two records are the same customer. Phone counts
for most of the score because after normalisation it is reliable; the name confirms or weakens
it. The score becomes a verdict: HIGH (auto-accept), LOW (flag for a human), or NONE (no
match).

The key idea: when the evidence is only partial, the system does not guess. It flags LOW for a
human. One-liner: "A silent wrong match is worse than an honest 'I am not sure', so partial
evidence is flagged for review rather than accepted."

### 4. The agent (tools, loop, memory, MCP)

On top sits an AI agent that answers plain-English questions ("how many returns need review?")
by calling the reconciliation functions as tools. The agent has no special knowledge; it
picks which tested function to call and states only what those functions return, so it cannot
invent a number.

One-liner: "The agent orchestrates tested tools rather than reasoning about raw data, so every
fact it states is grounded in a function I tested, not made up."

MCP is the standard way to expose those tools to any AI client; I built a small server that
does it. Memory lets it hold a conversation, so a follow-up question makes sense.

### 5. The guardrails (the trust layer)

The agent can propose three write-actions (confirm an order, hold an account, issue a refund
note) but can never execute one. A proposal is inert. A separate human confirmation step
re-checks it and is the only thing that writes to the database.

Things to defend:
- The agent structurally cannot act alone. One-liner: "There is no code path from the model to
  the database that skips a human."
- Refunds fail closed: refused unless a HIGH-confidence matched return backs it and the amount
  does not exceed the order, and refused when the amount cannot be verified. One-liner: "The
  last gate before money moves refuses what it cannot check."
- I re-validate at confirm time, not just when the proposal is made, because the world can
  change in between. One-liner: "The check that guards the database is the one next to the
  write, because state can drift between proposing and confirming."

### 6. Observability (see why it did what it did)

Every agent turn is recorded to a durable trace: each model call and tool call, its inputs,
outputs, latency, and any error. A crash is recorded and then re-raised, so a failure is both
visible and still loud. A confirmed action is written to an append-only audit table, linked to
the trace that led to it.

One-liner: "When the agent does something I can see exactly why, and when it fails the trace
captures the crash, because the failure is the run you most need to inspect."

### 7. Evals (measure whether it works, not just runs)

A set of known questions with known-correct answers, run automatically and scored. The
deterministic suite runs on every code change in CI and fails the build if any answer drifts.
A live suite runs real questions through the agent against the real model on demand.

Why it matters most: almost no portfolio measures its AI. This is the difference between "I
built an agent" and "here is the evidence it answers correctly, and it stays correct because a
regression fails the build". One-liner: "I gate every change on an eval suite, so correctness
is enforced, not asserted; I proved it by breaking one line and watching two eval cases fail."

## The habits worth naming (these are FDE signals)

- Fail closed: when unsure about a money action, refuse.
- Fail loud: record an error, then let it propagate; never hide a bug.
- Verify, do not trust: read the source, run the check, look at the real data, before believing
  something.
- Observability is additive: tracing never changes the behaviour it observes.
- Name the edges: I documented exactly what I did not build (RAG, auth, a log shipper) and why,
  which is more credible than implying everything is done.

## What I deliberately did not build (and can speak to)

- Retrieval over documents (RAG): a separate build, deferred to its own phase.
- Authentication on the confirm endpoint: so attribution is currently caller-asserted; operator
  identity is the next hardening step, and the trace link to hang it off is already there.
- A log shipper: failures are captured to the trace store but not yet sent to a log pipeline.

Being able to say what is missing and why is part of the answer, not a gap in it.
