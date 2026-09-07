"""Eval harness — scored, threshold-gated checks distinct from pytest's
plain pass/fail tests (constitution Principle V: "tests and evals are
distinct and both mandatory"). Extended in Phase 1 with real cases; the
Phase 0 skeleton (this module + backend/evals/cases/) is reused, not
rebuilt (research.md D7).

- `matching_correctness` reuses the constitution Principle II verdict-
  correctness fixture matrix (tests/services/test_hard_constraints_matrix.py,
  T034) rather than duplicating it — this is the same fixed set of
  (known profile x known scholarship) pairs, scored instead of just
  asserted. Its threshold is 1.0: constitution Principle II requires 100%
  of hard-constraint failures to be correctly reported as ineligible, so
  this case is a zero-tolerance CI score-regression gate, not a soft one.
- `qa_groundedness` (T056, backend/evals/cases/qa_groundedness.py) scores
  the Research/Q&A agent's confidence labeling against fixed, mocked
  question/source pairs.
"""

from collections.abc import Callable
from dataclasses import dataclass

THRESHOLD = 0.5
_MATCHING_CORRECTNESS_THRESHOLD = 1.0


@dataclass(frozen=True)
class EvalCase:
    name: str
    score_fn: Callable[[], float]
    threshold: float = THRESHOLD


def _score_matching_correctness() -> float:
    from app.agents.matching.agent import build_verdict
    from app.services.hard_constraints import evaluate_hard_constraints
    from tests.services.test_hard_constraints_matrix import _MATRIX

    if not _MATRIX:
        return 0.0

    correct = 0
    for case_factory in _MATRIX:
        profile, scholarship, expected_verdict, _ = case_factory()
        hard_outcomes = evaluate_hard_constraints(profile, scholarship)
        verdict = build_verdict(
            scholarship.id or "00000000-0000-0000-0000-000000000000", hard_outcomes, [], [], []
        )
        if verdict.eligibility_verdict == expected_verdict:
            correct += 1
    return correct / len(_MATRIX)


def _score_qa_groundedness() -> float:
    from evals.cases.qa_groundedness import score

    return score()


CASES: list[EvalCase] = [
    EvalCase(name="matching_correctness", score_fn=_score_matching_correctness, threshold=_MATCHING_CORRECTNESS_THRESHOLD),
    EvalCase(name="qa_groundedness", score_fn=_score_qa_groundedness),
]


def score_case(case: EvalCase) -> float:
    return case.score_fn()


def run_evals() -> bool:
    passed = True
    for case in CASES:
        score = score_case(case)
        ok = score >= case.threshold
        print(f"[eval] {case.name}: score={score:.3f} threshold={case.threshold} {'PASS' if ok else 'FAIL'}")
        if not ok:
            passed = False
    return passed


if __name__ == "__main__":
    import sys

    sys.exit(0 if run_evals() else 1)
