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
