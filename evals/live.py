# evals/live.py
"""
The live agent eval: does the whole agent, against real Claude, answer correctly?

Unlike the deterministic suite in run.py, this runs real questions through the actual
Conversation and the real Anthropic API, then scores each answer with a structured check
(does the answer contain the expected fact or verdict). It is intentionally NOT in CI: it
costs money, needs an API key, and is non-deterministic. It runs on demand to measure the
end-to-end agent, the LLM picking the right tools and composing a correct answer, which the
deterministic suite cannot cover.

Scoring is by structured substring / behaviour checks, not an LLM judge: each expected
answer contains a specific verifiable fact (a number, a refusal word), so a structured check
is deterministic enough, free, and defensible. An LLM judge would be the tool for genuinely
open-ended answers; it is deliberately not used here.

Run it (requires ANTHROPIC_API_KEY and a seeded database):
    python -m evals.live
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.conversation import Conversation


@dataclass
class LiveCase:
    id: str
    question: str
    # every string in must_contain (case-insensitive) has to appear in the answer
    must_contain: list[str]


LIVE_CASES: list[LiveCase] = [
    LiveCase(
        id="live-order-count",
        question="How many orders came in?",
        must_contain=["200"],
    ),
    LiveCase(
        id="live-return-count",
        question="How many returns are there?",
        must_contain=["40"],
    ),
    LiveCase(
        id="live-needs-review",
        question="Do any returns currently need human review?",
        # on the recoverable seed the answer is no / none / zero
        must_contain=["no"],
    ),
    LiveCase(
        id="live-quarantine",
        question="Were any rows quarantined or unable to be processed?",
        must_contain=["no"],
    ),
]


@dataclass
class LiveResult:
    case: LiveCase
    answer: str
    passed: bool
    missing: list[str]


def _score(case: LiveCase, answer: str) -> LiveResult:
    lower = answer.lower()
    missing = [needle for needle in case.must_contain if needle.lower() not in lower]
    return LiveResult(case=case, answer=answer, passed=not missing, missing=missing)


def run_live_evals() -> list[LiveResult]:
    """Run every live case through a fresh Conversation against real Claude."""
    results = []
    for case in LIVE_CASES:
        convo = Conversation()
        answer = convo.ask(case.question)
        results.append(_score(case, answer))
    return results


def format_live_report(results: list[LiveResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines = [
        "# OpsBridge live agent eval",
        "",
        f"**{passed}/{total} passed** (real Claude, non-deterministic)",
        "",
    ]
    for r in results:
        mark = "pass" if r.passed else "FAIL"
        lines.append(f"## {r.case.id}: {mark}")
        lines.append(f"Q: {r.case.question}")
        if r.missing:
            lines.append(f"Missing expected: {r.missing}")
        lines.append(f"A: {r.answer.strip()}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    results = run_live_evals()
    print(format_live_report(results))


if __name__ == "__main__":
    main()
