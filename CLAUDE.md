# CLAUDE.md: Working Agreement for This Repo

This file governs how coding agents (Claude Code) and I work in this repository.
It exists so that every change is deliberate, tested, and something I can defend
line by line in an interview. If a change would violate anything here, stop and ask.

## What this project is

OpsBridge is an AI agent that ingests a fragmented, messy B2B operations stack
(database plus spreadsheet export plus PDFs plus an undocumented partner API),
answers natural-language operations questions over it, and *proposes* write-actions
that a human confirms. It is built to demonstrate the three core Forward Deployed
Engineer "hats": Software Engineer, AI Engineer, and AI Solutions Architect.

The differentiator is hat 3: evals, observability, guardrails, deployment, and
documented decisions, not just a working agent.

## Voice and output rules (applies to everything the agent writes)

1. No em-dashes anywhere. Not in docs, code comments, commit messages, README, or
   generated text. Use a plain hyphen, a comma, a colon, or split the sentence.
2. No AI attribution of any kind. No "Generated with Claude Code", no co-author
   trailer in commits, no badges, no logos. This repo is my work and reads that way.
3. Write like a human engineer: plain, direct, specific. No marketing filler, no
   inflated adjectives, no "delve", "seamless", "leverage" as verbs.

## Golden rules

1. I am accountable for every PR. No unexplained code. If you generate it, you
   explain it in the PR description in plain language. No slop.
2. Tests are not optional. Every module with logic ships with tests. A phase is not
   "done" until its tests pass in CI.
3. Small, reviewable steps. One concern per change. Prefer a series of clear commits
   over one large dump I cannot review.
4. Never touch these without asking: schema_adapter/ core reconciliation logic
   (I write and own this by hand), guardrails/ policy rules, and anything under
   evals/ golden data. You may scaffold interfaces; I fill the judgment.
5. Log the decision. Any real tradeoff gets a one-line entry in DECISIONS.md.
6. Secrets never in code. Use .env; keep .env.example current. Never print or commit
   real keys.

## Stack (do not swap without a DECISIONS.md entry)

- Python 3.11, FastAPI, pydantic v2
- SQLAlchemy plus Postgres 16 (Docker locally)
- Anthropic API for the LLM (model pinned in config, not hardcoded per call)
- pytest for tests; ruff for lint
- GitHub Actions for CI (lint plus tests on every PR)
- Railway for deployment
- MCP server exposing the ops tools

## Project structure (canonical, keep it this way)

```
opsbridge/
  README.md            demo video, architecture, evals table, how-to-debug
  SOLUTION.md          discovery-style writeup (business problem, metrics, risks)
  DECISIONS.md         architecture decision log, one entry per real tradeoff
  ROADMAP.md           phased delivery plan tied to the five hats
  CLAUDE.md            this file
  legacy/              the seeded mess: DB seed, partner API, PDFs, spreadsheet
  schema_adapter/      canonical model plus reconciliation (hand-written, owned by me)
  agent/               tool-calling loop, tools, prompts, memory/state
  mcp_server/          MCP exposure of ops tools
  guardrails/          write-action policy plus human-in-the-loop proposal layer
  observability/       trace store plus /trace endpoint/view
  evals/               golden dataset plus scoring harness plus report
  api/                 FastAPI app wiring it together
  tests/
  .github/workflows/   CI
  .env.example
  docker-compose.yml   Postgres locally
```

## Coding standards

- Type everything. pydantic models for all data crossing a boundary.
- Functions do one thing; if a function needs a paragraph to explain, split it.
- No bare except. Catch specific exceptions; log with the trace_id.
- Config lives in one place (config.py / env), never scattered magic values.
- Every tool the agent can call is a typed, individually testable function.

## The agent's boundaries (safety model)

- The agent may read freely across all sources.
- The agent may propose exactly three write-actions: confirm order, hold account,
  issue refund note.
- The agent never executes a write on its own. It emits a structured proposal with a
  rationale; a human confirms in the UI; only then does the action commit.
- Every proposal and every confirmation is recorded in the trace.

## Definition of done (per phase)

- Code plus tests written, tests green in CI.
- Any tradeoff logged in DECISIONS.md.
- ROADMAP.md phase checkbox ticked.
- It runs locally via documented commands, and (from Phase 1) deploys to Railway.
