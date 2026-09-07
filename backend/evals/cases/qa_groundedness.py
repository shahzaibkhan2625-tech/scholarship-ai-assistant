"""Q&A groundedness eval case (constitution Principle V — "tests and evals
are distinct and both mandatory"). Scores the Research/Q&A agent
(app.agents.research_qa.agent.answer_question) on a fixed set of
question/source-chunk pairs against their expected confidence label
(verified/inferred/unknown).

The retrieve/LLM boundary is mocked exactly as in
tests/integration/test_qa_flow.py so this eval is deterministic and needs no
live network access — the point being scored is the agent's own grounding
logic (app.tools.ground_check, a deterministic non-LLM check), not whether
an LLM call happens to succeed on a given day."""

from dataclasses import dataclass
from unittest.mock import patch

from app.rag.retrieve import RetrievedChunk
from app.schemas.common import Confidence


def _chunk(text: str, source_url: str = "https://example.test/official-page") -> RetrievedChunk:
    return RetrievedChunk(text=text, source_url=source_url, score=0.9, retrieved_at="2026-01-01T00:00:00+00:00")


@dataclass(frozen=True)
class QaGroundednessCase:
    name: str
    question: str
    scholarship_id: str
    chunks: list[RetrievedChunk]
    llm_answer: str
    expected_confidence: Confidence


CASES: list[QaGroundednessCase] = [
    QaGroundednessCase(
        name="stated_stipend_answered_verified",
        question="What is the stipend?",
        scholarship_id="00000000-0000-0000-0000-000000000001",
        chunks=[_chunk("The monthly stipend for this scholarship is EUR 1200.")],
        llm_answer="The monthly stipend is EUR 1200.",
        expected_confidence=Confidence.VERIFIED,
    ),
    QaGroundednessCase(
        name="unmentioned_policy_is_unknown_not_guessed",
        question="Is my spouse covered?",
        scholarship_id="00000000-0000-0000-0000-000000000002",
        chunks=[_chunk("This scholarship covers full tuition and a monthly stipend.")],
        llm_answer="I could not confirm this from the official scholarship content.",
        expected_confidence=Confidence.UNKNOWN,
    ),
    QaGroundednessCase(
        name="ungrounded_confident_llm_guess_downgraded_to_unknown",
        question="Is my spouse covered?",
        scholarship_id="00000000-0000-0000-0000-000000000003",
        chunks=[_chunk("This scholarship covers full tuition and a monthly stipend.")],
        llm_answer="Yes, spouses and dependents are fully covered under this program.",
        expected_confidence=Confidence.UNKNOWN,
    ),
    QaGroundednessCase(
        name="synthesized_related_statements_labeled_inferred",
        question="What kind of applicant background is preferred?",
        scholarship_id="00000000-0000-0000-0000-000000000004",
        chunks=[
            _chunk(
                "Applicants must hold a Bachelor's degree in engineering with at least "
                "three years of relevant industry experience."
            )
        ],
        # Deliberately paraphrased (not verbatim) so lexical overlap with the
        # source lands in the "inferred" band, not "verified".
        llm_answer="Preferred applicants typically have engineering degrees and industry experience.",
        expected_confidence=Confidence.INFERRED,
    ),
    QaGroundednessCase(
        name="unrelated_question_no_chunks_retrieved_is_unknown",
        question="What career should I pursue in general?",
        scholarship_id="00000000-0000-0000-0000-000000000005",
        chunks=[],
        llm_answer="",
        expected_confidence=Confidence.UNKNOWN,
    ),
]


def run_case(case: QaGroundednessCase) -> bool:
    with (
        patch("app.agents.research_qa.agent.retrieve", return_value=case.chunks),
        patch("app.core.llm.generate_text", return_value=case.llm_answer),
    ):
        from app.agents.research_qa.agent import answer_question

        result = answer_question(case.question, case.scholarship_id)

    return result.confidence == case.expected_confidence


def score() -> float:
    """Fraction of fixed cases where the agent's confidence label matches
    the expected label. An empty case list scores 0.0 — never treated as a
    trivial pass."""
    if not CASES:
        return 0.0
    correct = sum(1 for case in CASES if run_case(case))
    return correct / len(CASES)
