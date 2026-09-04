# DECISIONS.md — Architecture Decision Log

One entry per real tradeoff. Keep them short: what we chose, what we rejected, why.
This is the cheapest way to prove hat-3 thinking — in an interview, "walk me through a
tradeoff" is answered by reading this file aloud.

Format:
### NNN — Title (date)
**Decision:** what we're doing.
**Alternatives:** what we didn't do.
**Why:** the reasoning.

---

### 001 — One deep project over a multi-project portfolio (2026-09-04)
**Decision:** Build a single flagship (OpsBridge) weighted heavily toward FDE hat 3.
**Alternatives:** A 3-project escalating portfolio; a flagship plus supporting repos.
**Why:** Depth on evals/observability/guardrails is the rarest signal and the one that
separates an FDE from an AI engineer. Three shallow projects would each stop at hat 1–2.
One project that visibly reaches hat 3 makes an enterprise reader take it seriously.

### 002 — Neutral B2B ops domain (2026-09-04)
**Decision:** Set the project in generic B2B operations (orders, returns, invoices).
**Alternatives:** FMCG/supply chain, compliance/GRC, healthcare.
**Why:** Widest enterprise appeal and instantly legible to any reviewer without domain
setup. The FDE hats we're proving (1–3) are domain-independent; domain depth (hat 4) is
signalled in SOLUTION.md rather than gating who understands the repo.

### 003 — Messy multi-source legacy stack, not a clean dataset (2026-09-04)
**Decision:** Seed a deliberately inconsistent stack: DB with messy columns/formats, a
returns spreadsheet with no shared key, mismatched PDFs, and an undocumented XML partner API.
**Alternatives:** A single clean CSV/Parquet ingest (the common tutorial shape).
**Why:** The FDE job is walking into someone else's mess and integrating. The seam between
messy legacy and clean canonical model is the whole point; a clean dataset removes the skill
being demonstrated.

### 004 — Three write-actions: confirm order, hold account, issue refund note (2026-09-04)
**Decision:** The agent can propose exactly three write-actions.
**Alternatives:** A single action (simplest); two actions.
**Why:** Three gives a richer guardrail story — different actions warrant different policy
checks (a refund note requires a matched return; a hold requires a reason). That variety is
what makes the safety layer worth showing, without sprawling into an unbounded action set.

### 005 — Human-in-the-loop, not policy-gated auto-action (2026-09-04)
**Decision:** The agent never executes a write on its own. It emits a structured proposal
with a rationale; a human confirms in the UI; only then does it commit.
**Alternatives:** Policy-gated auto-action within limits; a hybrid where policy auto-acts
and humans confirm only borderline cases.
**Why:** For a first deployment against real business records, "the agent proposes, the human
decides" is the trust model enterprises actually accept. It's also the more honest safety
story to demo, and it still exercises a full policy layer (the policy validates proposals and
blocks invalid ones before they ever reach the human).
