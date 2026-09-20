"""Anti-hallucination eval case (T096; constitution Principle V — "tests and
evals are distinct and both mandatory"; spec.md SC-004: "100% of factual
claims trace back to the user's profile or uploaded documents, zero
fabricated facts"). Mirrors `qa_groundedness.py`'s structure and pattern.

Scores whether generated CV/SOP claims are correctly identified as
traceable/untraceable against a fixed set of allowed-facts pools drawn from
representative `cv_gen`/`sop_gen` scenarios — a fully grounded CV bullet, a
fully grounded SOP restatement answering a specific question, a fabricated
achievement mixed in with a real fact, a numeric overstatement of a real
fact, and a claim with no supporting fact at all.

This exercises exactly `verify_claim_grounded` — the mechanism both
generation workflows' `ground_check` gate calls — with no DB and no live LLM
call needed, so it stays deterministic and free of the Gemini free tier's
daily quota (unlike the live-network contract tests in
`tests/api/test_generation_api.py`). A future change that weakens grounding
(e.g. reverting to the bare lexical `ground_check`, or loosening the
threshold) lowers this score below its 1.0 threshold and is blocked in CI,
exactly like `matching_correctness`'s zero-tolerance gate."""

from dataclasses import dataclass

from app.tools.ground_check import verify_claim_grounded


@dataclass(frozen=True)
class GenerationGroundednessCase:
    name: str
    facts: list[str]
    claims: list[str]
    expect_all_grounded: bool


CASES: list[GenerationGroundednessCase] = [
    GenerationGroundednessCase(
        name="cv_claim_fully_grounded_in_profile_education_fact",
        facts=["Education: BS in Computer Science from MIT with GPA 3.8/4.0."],
        claims=["I have a BS in Computer Science from MIT with GPA 3.8/4.0."],
        expect_all_grounded=True,
    ),
    GenerationGroundednessCase(
        name="sop_claim_fully_grounded_answering_a_specific_question",
        facts=["Education: BS in Computer Science from MIT with GPA 3.8/4.0."],
        claims=["I have a BS in Computer Science from MIT with GPA 3.8/4.0."],
        expect_all_grounded=True,
    ),
    GenerationGroundednessCase(
        name="fabricated_achievement_mixed_with_a_real_fact_is_caught",
        facts=["Education: BS in Computer Science from MIT with GPA 3.8/4.0."],
        claims=[
            "I have a BS in Computer Science from MIT with GPA 3.8/4.0.",
            "I won the Fulbright Award in 2020.",
        ],
        expect_all_grounded=False,
    ),
    GenerationGroundednessCase(
        name="numeric_overstatement_of_a_real_fact_is_caught",
        facts=["The applicant has 2 years of professional software engineering experience."],
        claims=["Has 5 years of professional software engineering experience."],
        expect_all_grounded=False,
    ),
    GenerationGroundednessCase(
        name="claim_with_zero_supporting_facts_is_caught",
        facts=["Education: BS in Computer Science from MIT with GPA 3.8/4.0."],
        claims=["I completed a PhD in Physics from Oxford."],
        expect_all_grounded=False,
    ),
]


def _all_grounded(facts: list[str], claims: list[str]) -> bool:
    return all(verify_claim_grounded(claim, facts).grounded for claim in claims)


def run_case(case: GenerationGroundednessCase) -> bool:
    return _all_grounded(case.facts, case.claims) == case.expect_all_grounded


def score() -> float:
    """Fraction of fixed cases where the grounding gate's grounded/blocked
    outcome matches the expected outcome. An empty case list scores 0.0 —
    never treated as a trivial pass."""
    if not CASES:
        return 0.0
    correct = sum(1 for case in CASES if run_case(case))
    return correct / len(CASES)
