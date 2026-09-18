# Evals

Measuring whether the agent works, not just whether it runs. There are two tiers, split by
what they can prove and what they cost.

## The two tiers

| | `evals/run.py` | `evals/live.py` |
| --- | --- | --- |
| What it exercises | the pipeline, the matcher and the guardrail policy | the whole agent against real Claude |
| Model calls | none | one conversation per case |
| Cost and speed | free, about a second | costs money, takes as long as the API does |
| Deterministic | yes, same seed, same score | no, the model phrases answers differently |
| Where it runs | every push and pull request, in CI | on demand, by hand |

The split is the point. The deterministic suite is cheap enough to gate every commit, so it
is the regression guard. The live suite is the only thing that proves the model picks the
right tools and composes a correct answer, so it exists, but it cannot sit in CI: it needs
an API key and would fail on wording rather than on substance.

## Running them

The deterministic suite needs a seeded database and nothing else:

```
python -m legacy.seed_db
python -m evals.run
```

It prints a markdown table and exits non-zero if any case failed, which is what makes it
usable as a CI gate. CI seeds first and then runs it, after lint and the test suite.

The live suite needs the same seeded database plus `ANTHROPIC_API_KEY` in your `.env`:

```
python -m evals.live
```

## Where the expected values come from

The golden expectations in `evals/golden.py` are not guesses. The pipeline counts (200
orders in, 200 canonicalized, 0 quarantined, 40 returns, 40 high-confidence matches) are
the values the real seed produces, and the seed is deterministic by construction, so the
same 200 rows appear on every machine. The policy expectations are the rules written down
in DECISIONS 028, 029 and 030: a pending order can be confirmed, a delivered one cannot, a
refund needs a high-confidence match to the row it cites, and a refund cannot exceed the
order amount.

That is what makes a failure meaningful. Nothing here asserts an opinion about what the
system should do. Every expected value is either a fact about the seeded data or a rule
recorded in the decision log, so a red eval means behaviour changed, and the report names
the case, the expected value and the actual one.

## Scoring

**Deterministic tier: exact comparison.** Each case produces a value (a count, or an
allow/refuse verdict) and is compared with `==` to its expected value. No partial credit and
no judgement, because every one of these answers is a verifiable fact.

**Live tier: structured checks.** Each live case lists the strings that must appear in the
answer, case-insensitively: a number like `200`, or a refusal word for a proposal the policy
must reject. That works because the questions are chosen to have verifiable answers, so a
correct answer cannot avoid containing the fact.

**On LLM judges.** An LLM judge is the right tool for scoring genuinely open-ended answers,
where many wordings are correct and no substring check can tell a good answer from a bad
one. It is deliberately not used here: it would add cost, latency and non-determinism to
score answers that contain a checkable fact. If the live suite grows cases like "explain why
this return is ambiguous", a judge is the escalation, and it should be introduced with its
own decision entry and its own rubric.

## A failure the evals caught

While hardening the refund rules, the amount comparison in `guardrails/policy.py` was
flipped from `cited_return.amount > order.amount` to `<`, which reads as a plausible typo
and is exactly the kind of inversion that survives a quick read.

The golden suite dropped to 13/15 and exited non-zero, naming both cases:

```
**13/15 passed**
| policy-refund-high-match-allowed  | policy | FAIL | True  | False |
| policy-refund-over-amount-refused | policy | FAIL | False | True  |
```

Both directions broke at once, which is what makes the report readable: a refund larger than
the order was allowed (the money bug), and a legitimate partial refund was refused (the
usability bug). Reverting the comparison restored 15/15. The unit tests catch this too; the
eval suite is what catches it without knowing which test to look at, and what would catch it
in CI on a branch nobody reviewed closely.
