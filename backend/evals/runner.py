"""Eval-harness skeleton. Real eval cases will live in evals/cases from Phase 1 onward."""

from dataclasses import dataclass

THRESHOLD = 0.5


@dataclass
class EvalCase:
    name: str


CASES: list[EvalCase] = [
    EvalCase(name="placeholder"),
]


def score_case(case: EvalCase) -> float:
    return 1.0


def run_evals() -> bool:
    passed = True
    for case in CASES:
        score = score_case(case)
        ok = score >= THRESHOLD
        print(f"[eval] {case.name}: score={score} threshold={THRESHOLD} {'PASS' if ok else 'FAIL'}")
        if not ok:
            passed = False
    return passed


if __name__ == "__main__":
    import sys

    sys.exit(0 if run_evals() else 1)
