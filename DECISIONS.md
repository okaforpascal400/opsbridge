# DECISIONS.md: Architecture Decision Log

One entry per real tradeoff. Keep them short: what we chose, what we rejected, why.
This is the cheapest way to prove hat-3 thinking. In an interview, "walk me through a
tradeoff" is answered by reading this file aloud.

Format:
### NNN: Title (date)
Decision: what we are doing.
Alternatives: what we did not do.
Why: the reasoning.

---

### 001: One deep project over a multi-project portfolio (2026-09-04)
Decision: Build a single flagship (OpsBridge) weighted heavily toward FDE hat 3.
Alternatives: A 3-project escalating portfolio; a flagship plus supporting repos.
Why: Depth on evals, observability, and guardrails is the rarest signal and the one
that separates an FDE from an AI engineer. Three shallow projects would each stop at
hat 1 to 2. One project that visibly reaches hat 3 makes an enterprise reader take it
seriously.

### 002: Neutral B2B ops domain (2026-09-04)
Decision: Set the project in generic B2B operations (orders, returns, invoices).
Alternatives: FMCG/supply chain, compliance/GRC, healthcare.
Why: Widest enterprise appeal and instantly legible to any reviewer without domain
setup. The FDE hats we are proving (1 to 3) are domain-independent; domain depth
(hat 4) is signalled in SOLUTION.md rather than gating who understands the repo.

### 003: Messy multi-source legacy stack, not a clean dataset (2026-09-04)
Decision: Seed a deliberately inconsistent stack: DB with messy columns/formats, a
returns spreadsheet with no shared key, mismatched PDFs, and an undocumented XML
partner API.
Alternatives: A single clean CSV/Parquet ingest (the common tutorial shape).
Why: The FDE job is walking into someone else's mess and integrating. The seam between
messy legacy and clean canonical model is the whole point; a clean dataset removes the
skill being demonstrated.

### 004: Three write-actions: confirm order, hold account, issue refund note (2026-09-04)
Decision: The agent can propose exactly three write-actions.
Alternatives: A single action (simplest); two actions.
Why: Three gives a richer guardrail story. Different actions warrant different policy
checks (a refund note requires a matched return; a hold requires a reason). That
variety is what makes the safety layer worth showing, without sprawling into an
unbounded action set.

### 005: Human-in-the-loop, not policy-gated auto-action (2026-09-04)
Decision: The agent never executes a write on its own. It emits a structured proposal
with a rationale; a human confirms in the UI; only then does it commit.
Alternatives: Policy-gated auto-action within limits; a hybrid where policy auto-acts
and humans confirm only borderline cases.
Why: For a first deployment against real business records, "the agent proposes, the
human decides" is the trust model enterprises actually accept. It is also the more
honest safety story to demo, and it still exercises a full policy layer (the policy
validates proposals and blocks invalid ones before they ever reach the human).

### 006: The fixture mess is generated in code, not loaded from a dump (2026-09-05)
Decision: legacy/seed_data.py builds every row in memory from a fixed seed, and
legacy/seed_db.py only writes what it is handed.
Alternatives: A checked-in SQL dump or CSV fixture; generating rows inline at insert
time inside the seeder.
Why: Splitting generation from writing makes the mess reproducible byte for byte,
testable with no database (most of the seed suite runs anywhere), and reusable by the
partner API, which builds its in-memory index from the same rows. A dump would be
opaque and there would be nothing to assert against.

### 007: Messy variants are guaranteed by construction, not by the random draw (2026-09-05)
Decision: For each varying field, build a plan holding an intended count per variant,
shuffle it with the seeded generator, then zip it with the rows.
Alternatives: Weighted random choice per row, trusting the fixed seed to cover
everything.
Why: The tests assert that every date format, phone format, amount shape and status
value is present. Under plain random choice those assertions pass by luck, and they
would start failing silently if the seed or the row count ever changed.

### 008: order_date, status and amount stay TEXT in one column each (2026-09-05)
Decision: Keep the damaged shapes in a single TEXT column per field, including amounts
written both as "N12,500" and as "12500".
Alternatives: Typed columns alongside a raw column; a separate column per shape.
Why: One text column holding both shapes is what the legacy system actually produces,
and normalising it is the whole reason Phase 2 exists. Typed columns would move the
problem into the seed and delete the exercise.

### 009: data/returns.csv is committed, not generated and ignored (2026-09-05)
Decision: Commit the generated returns export to the repository.
Alternatives: Add it to .gitignore and require a seed run before anyone can see it.
Why: It stands in for a spreadsheet somebody emailed over, not for build output.
Committing it lets a reader see the fuzzy-join problem without starting Postgres, and
it is deterministic, so it never produces a noisy diff.

### 010: Database tests are gated on a separate throwaway database (2026-09-05)
Decision: The database-backed seed tests skip unless OPSBRIDGE_TEST_DATABASE_URL names
a database. CI always sets it, so they always run there.
Alternatives: Point the tests at DATABASE_URL; wrap them in a transaction and roll back.
Why: The seed drops and recreates the entire legacy schema, so it cannot share a
database with development data, and DDL at that scope does not roll back cleanly inside
a fixture transaction. Gating on a separate variable makes destroying the wrong
database an explicit act rather than an accident.

### 011: Standardize on Python 3.14 (2026-09-05)
Decision: Use Python 3.14 as the project's single version, for both local dev and CI.
Alternatives: Install and pin Python 3.11 (the version originally scaffolded).
Why: The only interpreter available on the build machine is 3.14, and all pinned
dependencies install and pass on it (38 tests green). Chasing a 3.11 install added
friction with no benefit. The guiding principle is that local and CI must run the
same interpreter, so both are now 3.14. Revisit only if a dependency drops 3.14
support.

### 012: Missing customer name routes the row to quarantine, does not crash the batch (2026-09-05)
Decision: If a legacy order row has no usable name in either cust_name or customer, it
is rejected as a structured RejectedRow (source row plus reason) and excluded from the
canonical output, rather than failing the whole reconciliation. CanonicalOrder.customer_name
stays strict (min_length=1).
Alternatives: Relax the canonical model to allow empty names; or hard-fail the batch on
the first unnameable row.
Why: The canonical model is a clean contract; anything that reaches it must satisfy it.
Resilience to dirty data belongs in the pipeline around the model, not in a weaker
contract. Routing bad rows keeps the good data flowing and makes "how many rows could
not be canonicalized, and why" a measurable number.

### 013: Missing return reason is accepted, not rejected (2026-09-05)
Decision: A return with a blank reason is still valid. Store reason as "" canonically;
the presentation layer may display "unspecified". Do not invent a reason that was not
in the source.
Alternatives: Reject returns with no reason; or write "unspecified" into the canonical
data itself.
Why: Reason is not critical to a return's identity or to matching, so it should not
block canonicalization. Storing "" rather than a fabricated value keeps the canonical
data faithful to the source; display-time substitution avoids inventing information.

### 014: cust_name wins when both legacy name columns are populated (2026-09-05)
Decision: normalize_name uses cust_name when both cust_name and customer hold usable
values. Conflicting values are not combined or merged.
Alternatives: Prefer customer; concatenate both; flag the row for review.
Why: cust_name is the primary column in the legacy schema. The adapter's job is to
normalize source data, not invent new data, so merging two differing names (which would
fabricate a value present in neither column) is out of scope. A single deterministic
precedence rule is predictable and testable, and the choice is locked by
test_both_populated_cust_name_wins.

### 015: Slashed dates parse day-first (DD/MM/YYYY) (2026-09-06)
Decision: parse_order_date interprets the slashed format (e.g. "07/03/2026") as
DD/MM/YYYY, so 07/03/2026 is the 7th of March.
Alternatives: Parse as MM/DD/YYYY (US convention); reject slashed dates as too ambiguous.
Why: The format is genuinely ambiguous in isolation. Rather than guess, the ordering was
resolved from the data's own generator: legacy/seed_data.py line 207 emits
f"{value.day:02d}/{value.month:02d}/{value.year}", which is day-first. Parsing month-first
would silently corrupt every ambiguous date (any day <= 12) while appearing to succeed,
the most dangerous kind of bug. The rule is locked by test_parses_slashed_as_day_first
and test_slashed_unambiguous_day_still_day_first.

### 016: Unknown and blank statuses both map to UNKNOWN, not quarantine (2026-09-06)
Decision: normalize_status maps both an empty/blank status and any unrecognized status
value onto OrderStatus.UNKNOWN. Matching is case-insensitive and whitespace-tolerant
against a fixed table of known variants (confirmed, CONF, pending call, PENDING,
delivered, returned?).
Alternatives: Quarantine the row on an unknown status; guess the closest known status.
Why: Unlike a missing name, an unknown status does not make an order unusable, the
customer, amount, and date are still valid, so quarantining the whole row would discard
good data over one soft field. Recording UNKNOWN is the faithful answer: it neither
guesses nor throws away the order. Both the expected blank ("") and an unexpected value
(e.g. "cancelled") resolve to UNKNOWN; distinguishing them can be surfaced later as an
observability metric. Locked by test_empty_string_is_unknown and
test_unrecognized_value_is_unknown.

### 017: Phones normalize to +234 plus the last ten digits (2026-09-06)
Decision: normalize_phone strips a phone to digits, takes the last ten significant
digits, and renders them as "+234" followed by those ten digits. A value that cannot
yield ten digits returns None rather than raising.
Alternatives: Store the national ("0"-prefixed) form; keep each phone in its original
format; reject non-matching formats.
Why: The phone is the join key for the fuzzy matcher, so the same real number in any of
the three legacy formats (international, national, bare) must produce an identical
canonical string. All three wrap the same ten significant digits, so last-ten-digits
folding is the invariant that makes them equal, verified against the generator's own
_local_number_of (legacy/seed_data.py:240-245). Returning None on too-few-digits keeps
the normalizer from crashing the batch; the row builder decides whether a phone-less row
is quarantined. Locked by test_all_three_formats_collapse_to_same_string.

### 018: Amounts parse to Decimal via digit-stripping, safe because values are whole naira (2026-09-06)
Decision: parse_amount coerces input to string, strips to digits, and returns
Decimal(digits), or None when there are no digits. It never uses float.
Alternatives: Parse with float; strip only the known "N" prefix and commas rather than
all non-digits; reject non-numeric input by raising.
Why: Currency must be represented exactly, so Decimal is used rather than float, which
cannot represent many decimal values precisely. Digit-stripping is safe for this data
specifically because the generator emits whole-naira amounts with no kobo
(legacy/seed_data.py AMOUNT_MINIMUM..AMOUNT_MAXIMUM in steps of 50); if fractional
amounts were possible, digit-stripping would corrupt them and a decimal-aware parse would
be required. Returning None on no-digits keeps the normalizer from crashing the batch.
Locked by test_result_is_decimal_not_float and test_parses_naira_text_with_prefix_and_commas.

### 019: Required vs soft fields for order canonicalization (2026-09-06)
Decision: build_order treats name, phone, amount, and date as required: if any fails to
canonicalize, the whole row is quarantined as a RejectedRow whose reason names every
failed field. status is soft and never causes rejection; it always resolves to an
OrderStatus (UNKNOWN when blank or unrecognized, per DECISION 016).
Alternatives: Make every field required; make date or amount soft and keep the row with
a null; stop at the first failure rather than reporting all.
Why: A field is required when the record is meaningless or unmatchable without it. Name,
phone, amount, and date each fail that test: no name means the order cannot be attributed
or matched, no phone means it cannot be matched (phone is the join key), no amount or date
means core order identity is missing. status passes the test: an order with an unclear
state is still usable, so it resolves to UNKNOWN rather than blocking. Reasons list all
failed fields, not just the first, so the quarantine bucket is actionable for an ops
reviewer and countable as a Phase 6 metric. Locked by the rejection tests in
test_build_order.py, including test_multiple_failures_named_in_reason.

### 020: Return canonicalization mirrors orders, with reason soft and no date (2026-09-06)
Decision: build_return requires name, phone, and amount; any failure quarantines the row
as a RejectedRow naming the failed field(s). Returns have no date and no order_id, so the
source row index identifies the return. reason is soft (DECISION 013): blank or missing
reason is stored as "" and never causes rejection.
Alternatives: Require a reason; reject returns with an unmatched shape; carry a null
reason instead of "".
Why: Returns share the orders' required-vs-soft principle: name, phone, and amount are
each needed for the record to be meaningful and matchable, while reason does not affect a
return's identity or its match to an order, so it stays soft. The single-name column is
reconciled through the same normalize_name helper by passing the second column as None.
Storing "" rather than null for a missing reason keeps the canonical data faithful without
inventing a value. Locked by the tests in test_build_return.py, including
test_blank_reason_is_accepted_as_empty_string.

### 021: rapidfuzz for string similarity; scoring and thresholds are hand-written (2026-09-06)
Decision: Use rapidfuzz (pinned 3.14.6) for raw name-string similarity in the fuzzy
matcher. The confidence scoring, the weighting of phone vs name, and the HIGH/LOW/NONE
thresholds are written by hand, not taken from any library default.
Alternatives: Hand-roll the string-distance algorithm; use a heavier record-linkage
library; blend name and phone with equal weight.
Why: String-distance math (Levenshtein and its variants) is a solved, well-optimized
problem, so a vetted library is the right tool and hand-rolling it would add risk without
insight. The judgment that matters, and that an FDE is paid for, is how to combine
signals and where to draw the accept/flag/reject lines; that logic stays hand-written and
testable. Phone is weighted as the strong identity signal because normalize_phone makes
exact comparison reliable, while names are deliberately noisy in the data, so name
similarity confirms or flags rather than decides.

### 022: Match scoring weights and HIGH/LOW/NONE thresholds (2026-09-06)
Decision: _score_candidate blends an exact-phone signal (weight 0.7) with a name
similarity signal (rapidfuzz token_sort_ratio, scaled 0.0-1.0, weight 0.3).
match_return_to_orders scores every candidate, takes the highest, and maps the score to
a verdict: >= 0.85 is HIGH (auto-accept), >= 0.5 is LOW (flag for human review), below
0.5 is NONE (no match, matched_order_id is None). An empty candidate list is NONE.
Alternatives: Equal weighting of name and phone; hard-gating on an exact phone match;
a single accept/reject threshold with no review tier.
Why: Phone is the reliable identity signal because normalize_phone canonicalizes all
formats, so it carries most of the weight; names are deliberately noisy, so name
similarity confirms rather than decides. The thresholds follow from the weights: a phone
match alone scores 0.7, which is deliberately treated as LOW (flag), not HIGH, because a
matching phone with a mismatched name is exactly the ambiguous case a human should see.
The LOW tier is the honest-uncertainty middle, consistent with the human-in-the-loop
model in DECISION 005: a silent wrong match is worse than an admitted "not sure". Every
match carries a rationale so a reviewer can act without re-deriving the decision. Locked
by test_phone_match_name_mismatch_is_low and the threshold tests in test_match_return.py.

### 023: Pipeline isolates I/O from logic; hard-case seed deferred to Phase 6 (2026-09-06)
Decision: schema_adapter/pipeline.py is the only module that touches I/O (Postgres for
orders, the CSV for returns); reconcile.py stays pure and I/O-free. Against the seeded
data the matcher returns 40/40 HIGH, which is correct because every seeded return
corresponds to a real order whose phone normalizes to an exact match. The LOW and NONE
tiers are exercised and proven by the unit tests, not by the seed. A labeled golden
dataset with deliberate hard cases (orphans, name-only ambiguous) is deferred to Phase 6,
where the eval harness will consume it.
Alternatives: Rebuild the seed now to force LOW and NONE outcomes; blend I/O into the
reconcile module.
Why: Keeping logic pure and I/O in a thin shell makes the reconciliation fully testable
without a database and is the reason the unit tests need no fixtures. The all-HIGH result
is honestly explained rather than engineered away: on recoverable data, recovering
everything is the right answer, and the discrimination tiers are demonstrated in the test
suite (test_phone_match_name_mismatch_is_low, test_no_plausible_match_is_none). Building
the golden dataset now, before the eval harness that reads it exists, would be premature;
it belongs with Phase 6 where it earns its keep.

### 024: Agent loop design - injected client, tool dispatch, iteration guard (2026-09-07)
Decision: agent/loop.py exposes ask(question, client=None). The Anthropic client is
injected and defaults to a real one, so the loop is tested with a stub (no network, no
cost, deterministic in CI). A single _TOOL_FUNCTIONS dict is the source of truth for both
the schemas sent to Claude and the dispatch when Claude requests a call. The loop runs
until the model returns a final answer or a max-iteration guard (8) stops it. The model
is Claude Sonnet 5, pinned in config. Phase 3 tools are read-only.
Alternatives: A globally constructed client (simpler, untestable without the network);
Opus or Fable for the model (more capable, materially more expensive for simple
tool-orchestration); no iteration guard.
Why: Injecting the client is what makes the loop unit-testable, so the tool-wiring is
verified for free rather than by paying for live calls. Sonnet is chosen because the
agent orchestrates tools rather than doing heavy reasoning, so the mid-tier model is
capable and much cheaper, and it is one config line to change. The iteration guard stops
a misbehaving model from looping forever. The agent has no special knowledge: it only
knows the tools and composes their results, so every fact it states is grounded in a
tested function rather than invented. Loop mechanics locked by the stubbed tests in
test_loop.py.

### 025: Multi-turn sessions via a Conversation over a shared run_turn (2026-09-07)
Decision: The core loop is extracted into run_turn(messages, client), which runs one
tool-calling turn against a caller-supplied message list. ask() is a thin wrapper that
calls run_turn with a fresh single-question list. agent/conversation.py adds a
Conversation class that owns a growing message list and appends each question, the
tool_use and tool_result turns run_turn adds, and the final answer, so a later turn sees
the full history. Context assembly is explicit: the whole message list is the context,
nothing is summarized or dropped. A turn is all-or-nothing: it runs on a working copy that
replaces the history only when the model ends it with a non-empty answer; if a tool or
the model call raises, the answer is empty, or the iteration guard stops the loop, the
history is left unchanged.
Alternatives: Duplicate the loop in a separate conversation function; summarize old turns
to save tokens; store history in a database; append to the live history and repair it
after a failure.
Why: Extracting run_turn lets one-shot and multi-turn share exactly one loop
implementation, so there is no drift between them. Keeping the full history as the context
is the simplest correct behavior for a session of this size and is easy to reason about
and test; token-budget summarization is a later optimization that is not needed yet.
Holding state in memory (not a database) is right for an interactive session and keeps the
Conversation testable with a stub. The all-or-nothing turn exists because the API rejects
a tool_use with no tool_result and an empty assistant message: without it, one database
outage mid-turn would leave history that fails every later request. The guard's notice is
not kept because the model never said it. Locked by test_conversation.py, including
test_second_turn_sees_the_first_exchange, test_tool_turns_are_kept_in_history, and
test_failed_tool_leaves_history_unchanged.

### 026: MCP server as a thin adapter; mcp SDK pinned to v1 (2026-09-07)
Decision: mcp_server/server.py exposes the four existing read tools over the Model Context
Protocol using the FastMCP high-level API. It is a thin adapter: each MCP tool calls the
corresponding function in agent/tools.py, which is unchanged and already tested. The
adapter adds only two protocol-level behaviors: each list result is wrapped in one JSON
object, and each tool runs in a worker thread. The mcp dependency is pinned to 1.30.0, the
latest 1.x release, not the v2 line.
Alternatives: Use the mcp v2 SDK (MCPServer); use the separate standalone fastmcp package;
reimplement the tool logic inside the server; return bare lists and run the tools on the
event loop.
Why: Keeping the server a thin adapter means the tested tool logic stays in one place and
the MCP layer only advertises it, so the tests cover only what the adapter adds. The two
protocol behaviors exist because of how FastMCP v1 works: it sends a bare list as one text
block per item and an empty list as no text at all, so a client reading the text would see
nothing where the answer is "none"; and it calls a sync tool directly on the event loop,
where a slow database connect would stop the server from answering anything else. The v1
pin is deliberate: mcp v2 (released 2026-07-28) is a breaking change that renamed FastMCP
to MCPServer and removed mcp.server.fastmcp, and an unpinned install now resolves to v2,
which would break the import. Pinning to 1.30.0 keeps the build reproducible and defers a
v2 migration to a moment chosen on purpose rather than forced by a resolver. The cost:
upstream now treats 1.x as a maintenance line that gets only critical bug and security
fixes, and an exact pin will not pick those up, so it is bumped by hand; moving this
adapter to v2 is an import and class rename. Locked by test_mcp_server.py, including
test_each_tool_calls_its_own_function and test_empty_list_result_is_one_json_text_block.

### 027: Write actions record to a new opsbridge.actions table; legacy is never mutated (2026-09-18)
Decision: The three write actions (confirm_order, hold_account, issue_refund_note) are
recorded in a new opsbridge.actions table in a separate schema. The legacy.orders table is
treated as read-only source data and is never altered.
Status: designed here. The table and the code that writes to it land with the confirmation
endpoint; what exists today is the proposal contract and the policy gate.
Alternatives: Add status/flag columns to legacy.orders and update rows in place.
Why: An integration should not alter the source system's schema; in a real deployment you
rarely have permission to, and doing so risks the source data's integrity. Recording
actions in a separate audit table keeps legacy untouched, gives an immutable trail of who
did what and why, and feeds observability later. It is the honest model of how an external
agent acts on a system it does not own.

### 028: Policy is validated at both propose time and confirm time (2026-09-18)
Decision: The same pure validate_proposal runs when a proposal is created (propose time)
and again immediately before the write commits (confirm time). It also rejects a proposal
whose rationale is blank once stripped: the model's min_length=1 counts whitespace as
content, so the gate is what enforces the ROADMAP rule that an action requires a reason.
Alternatives: Validate only at propose time; validate only at confirm time.
Why: Propose-time validation is a courtesy filter, a human never sees an impossible
proposal. But it is not a guarantee, because state can drift between propose and confirm (a
match could change, an order could be re-processed). The confirm-time re-check is the only
validation that actually guards the database, so it must exist. Using one pure function for
both keeps a single source of truth for "is this allowed". A refund note additionally
requires a HIGH-confidence backing match to the exact return row it cites, so a match the
system flagged as uncertain cannot be laundered into a money movement. Locked by
test_policy.py, including test_refund_on_low_confidence_match_is_rejected,
test_refund_when_the_cited_return_row_has_no_match_is_rejected and
test_blank_rationale_is_rejected.

### 029: Confirm status rule, refund amount ceiling, and their deliberate scope (2026-09-18)
Decision: Two policy rules were added beyond order-existence and the HIGH-confidence match.
(1) confirm_order is allowed only when the order status is PENDING or UNKNOWN; a CONFIRMED,
DELIVERED, or RETURNED order is rejected, because confirming an order that has already moved
past confirmation records an action that says nothing true. (2) A refund note is rejected
when the cited return's amount exceeds the order amount; equal or less is a full or partial
refund and is allowed. That ceiling is checked only when the caller passes the returns list
and the cited row is in it, because returns defaults to empty; the HIGH-confidence match is
required either way. The scope of the status rule is deliberate and tested: it applies to
confirm only, so a delivered or returned order can still be refunded or held (you refund
based on a valid matched return, not fulfillment status); and an UNKNOWN status is
confirmable, which per DECISION 016 includes both blank and unrecognized source statuses, on
the grounds that confirm commits nothing on its own and a human reviews the rationale.
Alternatives: Make confirm status-blind; block refunds and holds on terminal statuses too;
treat UNKNOWN as non-confirmable.
Why: These are the checks a reviewer expects on a money and state action. Blocking a
re-confirm keeps the audit trail honest. Capping the refund at the order amount stops any
single refund note paying out more than the order was worth. It is a per-note ceiling, not a
running total: the gate is stateless and holds no record of notes already issued, so two
returns that both match one order can each pass, and aggregate exposure per order is a
separate check that lands with the actions table in DECISION 027. Scoping the status rule to
confirm, and asserting that scope with tests (test_terminal_status_does_not_block_refund,
test_terminal_status_does_not_block_hold), stops the natural refactor of hoisting that check
above the action if-chain from silently changing refund or hold policy. The amount ceiling
has no equivalent scope test yet. Locked by test_policy.py, including
test_confirm_delivered_order_is_rejected and test_refund_above_the_order_amount_is_rejected.

### 030: confirm() is the sole write path; propose validates, confirm re-validates and writes (2026-09-18)
Decision: guardrails/actions.py splits the flow in two. propose() runs the policy and
returns the verdict, touching nothing. confirm() re-runs the same policy at confirm time
and writes an audit row to opsbridge.actions ONLY when the policy allows. confirm() is the
only function that writes to the actions table, so no action can be recorded without
passing confirmation. It validates the state it is handed rather than re-reading the
sources, so the caller must pass state read at confirm time, and a refund note must carry
its cited return, because the amount ceiling is skipped when that return is missing
(DECISION 029) and a skipped ceiling next to a write is not acceptable. The table is
created with IF NOT EXISTS in a separate opsbridge schema and is never dropped, and
nothing here updates or deletes a row, so the trail only grows; the legacy schema is never
touched.
Alternatives: A single act() that validates and writes in one call; writing at propose
time and rolling back on rejection; storing actions by mutating legacy.orders.
Why: Separating propose from confirm makes the human-in-the-loop model enforceable rather
than aspirational: the agent can only ever produce an inert proposal, and a write requires
a distinct confirm step. confirm re-validates instead of trusting the earlier propose
because state can drift in between (DECISION 028), so the check that actually guards the
database is the one next to the write. Append-only in a separate schema keeps an honest
record and leaves the source data intact (DECISION 027), but the table is append-only by
construction, not by permission: the application role still holds UPDATE and DELETE, so
revoking them is a deployment step rather than something this code can claim. The row
records what was confirmed and why, not who confirmed it; confirmed_by and trace_id land
with the confirmation endpoint. That endpoint is still open in ROADMAP Phase 4, so this
supersedes 027's Status line: the table and its write path landed ahead of it. Locked by
test_actions.py, including test_confirming_a_rejected_proposal_writes_nothing,
test_confirm_revalidates_against_state_that_drifted_since_propose, and
test_propose_never_writes_even_when_allowed.

### 031: /confirm HTTP endpoint reuses the audited confirm path (2026-09-18)
Decision: api/main.py adds POST /confirm, the HTTP surface for a human to confirm a
proposed action. The route validates the request body with a pydantic model that forbids
unknown fields and caps the rationale (a malformed body is a 422), builds an
ActionProposal, and calls the same guardrails.actions.confirm() used everywhere else. The
server fetches confirm-time reconciliation state itself and validates against it; the
client sends only the proposal. A policy-refused proposal returns 200 with allowed=false,
and only an allowed one writes one audit row.
Alternatives: A new endpoint-specific write path; trusting proposal state sent by the
client; returning an error status for a policy refusal.
Why: Reusing confirm() means the HTTP path has no write path of its own, so every guardrail
(re-validation, the refund rules, append-only audit) applies to HTTP callers without being
re-implemented or able to drift. The server reading its own confirm-time state honors
DECISION 028: a client cannot smuggle a stale or forged snapshot to slip a proposal past
the policy. A refusal is a valid business outcome, not a server error, so it is a 200 with
allowed=false, while a malformed request is a 422 caught at the boundary.
Scope, deliberately left open. The endpoint has no authentication: anything that can reach
the port can confirm, so the "human" in human-in-the-loop is whoever holds network access
until an auth layer lands. It records no operator identity, which supersedes DECISION 030's
expectation that confirmed_by and trace_id would arrive with the endpoint; they move to
Phase 5 with the trace store. It is not idempotent either: the same body posted twice
writes two audit rows, and the confirm_order status rule cannot prevent that, because
DECISION 027 forbids mutating legacy.orders, so a confirmation never changes the status the
rule reads. An infrastructure failure (database unreachable, returns export missing) is
currently an unlogged 500, which Phase 5 turns into a logged failure with a trace id.
Locked by test_confirm_endpoint.py, including test_refused_confirm_writes_nothing and
test_valid_confirm_writes_one_row, which both drive the real route.

### 032: run_turn tracing observes without changing behavior; failures are recorded then re-raised (2026-09-18)
Decision: run_turn takes an optional trace: Trace | None = None. When None (the default,
so every existing caller is unchanged), nothing is traced and the returned answer is
identical. When a Trace is passed, run_turn records a step per model call and per tool call
(name, input, output, latency, error) and sets final_answer, but never changes control flow
or the returned string, and never touches the database: the caller owns persistence. A tool
that returns an {"error": ...} dict records that on its step; a tool that raises records a
step with error=str(exc) and its real measured latency, then the exception is re-raised, so
a crash is both visible in the trace and still fails loud. On the iteration-guard path the
trace's final_answer is set to the limit message; on the raising path final_answer stays
empty by design, because the turn produced no answer and the failing step carries the error.
Alternatives: Bake tracing into the loop unconditionally; persist inside run_turn; swallow
tool exceptions once recorded.
Why: Keeping tracing optional and behavior-identical means observability is orthogonal to
logic, proven by the existing loop tests passing unchanged and a test asserting the same
answer with and without a trace. Keeping persistence in the caller keeps run_turn pure and
database-free. Recording a raising tool before re-raising is the whole point of
observability: the failure you most need to inspect is a crash, and it must be captured
without being hidden. The failure handler catches Exception broadly, a documented exception
to the catch-specific rule, because any failure deserves a trace step and narrowing it would
drop the ones you most need. Locked by test_loop.py, including
test_a_raising_tool_is_recorded_and_still_raises and the same-answer-with-and-without-trace test.

### 033: Conversation persists a trace per turn, tolerant of both crashes and save failures (2026-09-18)
Decision: Conversation.ask creates a Trace per turn, passes it to run_turn, times the whole
turn, and persists it via save_trace. Two failure paths are handled so tracing never
interferes with the conversation. First, the turn runs in a try and the save runs in a
finally, so a turn that raises still persists its trace (with the failing step and the time
it ran before failing), which is the trace most worth inspecting; the exception still
propagates. Second, save_trace is wrapped so a persistence failure is swallowed: losing a
trace is acceptable, losing an answer the agent already produced is not. Persistence is on by
default (right for real use) and the stubbed unit tests opt out, so they never write to a
database. last_trace_id is set before the turn so the trace of a raising turn is findable.
Alternatives: Save only on success (loses the crash trace); let a save failure propagate
(breaks a good turn); resolve an engine unconditionally (silently writes traces from every
stubbed test into the dev database).
Why: Observability must observe without interfering. Saving in a finally captures the exact
turn a developer most needs, the one that failed. Swallowing a save failure keeps tracing
additive: the user still gets their answer even if Postgres is down. The swallow catches
Exception broadly (a documented exception to the catch-specific rule, the second after
DECISION 032) because any persistence failure must be prevented from breaking a good turn;
it is silent until Phase 5 structured logging gives it somewhere to report. Locked by
test_conversation.py, including test_a_raising_turn_still_persists_its_trace and
test_a_save_failure_does_not_break_the_turn.

### 034: Audit rows carry an optional trace_id, correlated but not enforced (2026-09-18)
Decision: The opsbridge.actions table gains a nullable trace_id column so a confirmation can
be traced back to the agent turn that proposed it. It is nullable, not required, because a
human can confirm directly through the HTTP endpoint with no agent turn behind it. It is a
plain TEXT string, not a foreign key to opsbridge.traces, so an audit write never fails on a
trace reference that was never saved, which matters because DECISION 033 lets a trace save
fail silently: the action must still be recorded even if its trace was lost. The column is
added by an idempotent ALTER TABLE ADD COLUMN IF NOT EXISTS in ensure_actions_table, so a
database created before this change repairs itself on the next call rather than 500-ing,
since CREATE TABLE IF NOT EXISTS never alters an existing table. The endpoint caps trace_id
at 64 characters, matching the rationale cap.
Alternatives: A required trace_id (breaks direct human confirms); a foreign key to the
traces table (an audit write would fail when a trace was not persisted); a manual migration
step (a database that predates the change 500s until someone remembers to run the SQL).
Why: The correlation is what lets an auditor answer "which turn produced this row", closing
a real gap DECISION 031 had deferred. Nullable and unenforced is the honest model given a
turn may not exist and a trace may not have saved; the audit record's own integrity comes
first. The self-healing DDL is the lightweight migration this project's scale warrants, and
it is idempotent, verified by test_an_older_table_repairs_itself. Two limits are deliberately
left open: attribution is caller-asserted (any client can stamp any id) and nothing outside
tests supplies a trace_id yet, both bounded by the same missing authentication layer as the
deferred confirmed_by field. Locked by test_actions.py including
test_an_older_table_repairs_itself.

### 035: Two eval tiers, a deterministic gate in CI and a live agent run on demand (2026-09-18)
Decision: Evals are split in two. evals/run.py scores the pipeline, the matcher and the
guardrail policy against golden expectations with no model in the loop, exits non-zero on
any failed case, and runs in CI on every push and pull request after lint and the tests, with
an explicit seed step before it. evals/live.py runs real questions through the agent against
the Anthropic API and is run by hand, never in CI. Golden expectations are read from the real
seed and from the rules already recorded in DECISIONS 028, 029 and 030, not guessed. Scoring
is exact comparison in the deterministic tier and structured substring checks in the live
tier; an LLM judge is named as the escalation for open-ended answers and deliberately unused.
Alternatives: One suite that always calls the model (honest but too slow, costly and flaky
to gate commits); no CI gate, running evals by hand (a regression lands and nobody notices);
an LLM judge for every case (cost and non-determinism to score verifiable facts); letting the
tests leave the database seeded rather than seeding explicitly (makes the evals depend on
pytest collection order).
Why: A regression guard has to be cheap enough to run on every commit, and the deterministic
tier is: it calls no model, so it is free, fast and gives the same score every time. The live
tier is the only thing that proves the model picks the right tools and composes a correct
answer, which is worth measuring but cannot gate a build that must pass without an API key.
Reading expectations from the real seed is what makes a red eval meaningful: every value is
either a fact about the seeded data or a rule in the decision log, so a failure means
behaviour changed rather than an opinion being violated. Structured checks beat a judge for
answers that contain a checkable fact, because a number is either present or it is not; a
judge earns its cost only when many wordings are correct, and adopting one deserves its own
entry and rubric. The gate is demonstrated, not assumed: flipping the refund amount
comparison from > to < took the suite to 13/15 and exited non-zero, naming both sides of the
break (policy-refund-over-amount-refused went from refuse to allow, and
policy-refund-high-match-allowed from allow to refuse); reverting restored 15/15.
