# ROADMAP.md: OpsBridge Delivery Plan

A phased plan, sequenced the way a Forward Deployed Engineer delivers: prove the seam
early, deploy early, and weight the effort toward the things that make an agent
trustworthy in production, which are evals, observability, and guardrails.

Each phase maps to the FDE hats it demonstrates. Target: a portfolio piece that reads
as "this person is already operating at FDE level", not "strong junior dev".

Legend: [ ] not started, [~] in progress, [x] done

---

## Phase 1: Foundation and the mess. Hats: 1
Prove the software-engineering fundamentals and stand up the messy legacy world the
agent will later have to tame. Deploy something live on day one.

- [x] Repo skeleton matching CLAUDE.md structure
- [x] Postgres via docker-compose
- [x] Seed a deliberately messy legacy.orders table (inconsistent columns, mixed date
  formats, free-text statuses, mixed phone/amount formats)
- [x] Seed a returns spreadsheet with no shared key (fuzzy join only)
- [ ] Seed mismatched invoice PDFs into data/docs/
- [x] legacy/partner_api.py, undocumented partner delivery API (returns XML)
- [x] FastAPI app with /health
- [~] CI: ruff plus pytest on every PR (workflow written, not yet run on GitHub)
- [ ] Deploy /health to Railway (deploy early, not last)
- [x] Seed tests asserting the mess actually exists

## Phase 2: Schema adapter (the seam). Hats: 1, 3 (data curation)
The interview centerpiece. Hand-written by me. Reconciles every messy source into one
canonical model. This is the "integrate into a messy existing stack" proof.

- [x] CanonicalOrder / CanonicalReturn pydantic models
- [x] Normalizers: dates (3 formats to ISO), phones (to E.164-ish), amounts, statuses
- [x] Fuzzy join: returns to orders on name plus phone, with a confidence score
- [x] Unresolved-match handling (never silently guess; flag low confidence)
- [x] Tests covering every messy variant seeded in Phase 1
- [x] DECISIONS.md entries for the reconciliation choices

## Phase 3: Agent, tools, RAG, MCP. Hats: 2
The AI-engineering core. Tool-calling agent over the canonical layer, with retrieval
over the PDFs and the tools exposed via MCP.

- [x] Read tools: get_operations_summary, get_unmatched_returns, get_flagged_returns,
  get_quarantined_rows
- [x] Tool-calling loop with the Anthropic API; model pinned in config
- [x] Session memory/state plus a documented context-assembly step
- [x] MCP server exposing the read tools
- [x] Tests per tool plus a loop-level test with a stubbed model

### Deferred from Phase 3
Planned for this phase but not built. The read tools that shipped are reconciliation tools
over the orders table and the returns export. These two are deferred, not dropped:

- [ ] get_delivery_status: read tool over the partner delivery API (parses its XML)
- [ ] search_docs: RAG over the invoice PDFs

RAG is deferred to a dedicated later phase because it needs the invoice PDFs (the Phase 1
seed item, still open) and a vector store. The partner-API tool is deferred with it.

## Phase 4: Guardrails and human-in-the-loop. Hats: 2, 3 (safety/security)
Make "trusted to act" real. The agent proposes; the human confirms.

- [x] Three proposal types: confirm_order, hold_account, issue_refund_note
- [x] Structured proposal object with rationale plus affected records
- [x] Policy checks (for example refund note requires a matched return; hold requires a reason)
- [x] Confirmation endpoint, only a confirmed proposal commits the write (POST /confirm
  reuses confirm(), the single audited write path)
- [x] Tests: no write path exists that bypasses confirmation
- [x] DECISIONS.md entry for the safety model

## Phase 5: Observability. Hats: 3 (observability)
See why the agent did what it did. The debugging story Ed calls "so important".

- [~] trace_id per request threaded through every step (one trace id per agent turn, on
  every step of that turn, and an audit row can carry it; HTTP requests are not traced, and
  nothing outside a client that posts trace_id to /confirm supplies one yet)
- [~] Structured logs: each decision, tool call, input/output, latency, failures (all of
  it recorded to the trace store; no logger is wired, so nothing reaches stdout or a log
  aggregator, and swallowed failures are still silent)
- [x] /trace/{id} returning the full timeline
- [~] A minimal trace view for the demo video (the timeline is served as JSON by
  /trace/{id}; a visual view is deferred)

## Phase 6: Evals. Hats: 3 (evals). DO NOT SKIP
The rarest signal in any portfolio. Measure whether the agent works, not just runs.

- [x] Golden dataset of ops questions plus expected answers/behaviours
- [x] Scoring harness (exact plus judged where needed), reproducible via one command
- [x] Results table generated into the README
- [x] At least one documented failure the evals caught, and the fix
- [x] DECISIONS.md entry for the eval methodology

## Phase 7: Package and broadcast. Hats: 3, signals 4 and 5
Make an enterprise able to reach out.

- [ ] SOLUTION.md, discovery-style writeup: problem, success metrics, data, risks
- [ ] 90-second demo video at the top of the README (question, proposal, confirm, trace)
- [ ] README: architecture, evals table, how to debug it, one-command run plus deploy
- [ ] Final Railway deploy verified reproducible
- [ ] LinkedIn post applying Ed's five-hats framework, tag Ed, link repo plus demo

---

## Sequencing notes
- Deploy in Phase 1 and keep it deployable every phase after. Never leave deploy to the end.
- Phase 2 is written by hand; it is the piece I must defend cold.
- If time compresses, protect Phases 4 to 6. The agent alone is commodity; the guardrails,
  observability, and evals are what earn the FDE read.