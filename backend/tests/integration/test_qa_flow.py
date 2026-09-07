"""Integration test covering spec.md US3 Acceptance Scenarios 1-4. The
retrieve/LLM boundary is mocked so scenarios are deterministic."""

import uuid
from unittest.mock import patch

from app.rag.retrieve import RetrievedChunk


def _chunk(text: str, source_url: str = "https://example.test/official-page") -> RetrievedChunk:
    return RetrievedChunk(text=text, source_url=source_url, score=0.9, retrieved_at="2026-01-01T00:00:00+00:00")


def test_scenario_1_stated_stipend_answered_verified_with_citation(authed_user) -> None:
    """Given a scholarship with a stated stipend amount, When the user asks
    "what is the stipend?", Then the system answers with the amount, labels
    it Verified, and cites the official source and last-verified date."""
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()
    chunk = _chunk("The monthly stipend for this scholarship is EUR 1200.")

    with (
        patch("app.agents.research_qa.agent.retrieve", return_value=[chunk]),
        patch("app.core.llm.generate_text", return_value="The monthly stipend is EUR 1200."),
    ):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa", headers=headers, json={"question": "What is the stipend?"}
        )

    body = response.json()
    assert body["confidence"] == "verified"
    assert "1200" in body["answer_text"]
    assert body["source_url"] == "https://example.test/official-page"
    assert body["last_verified_at"] is not None


def test_scenario_2_unmentioned_policy_answered_unknown_not_guessed(authed_user) -> None:
    """Given a scholarship whose page does not mention spousal/dependent
    policy, When the user asks about it, Then the system answers
    "Unknown / could not confirm" rather than guessing."""
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()
    chunk = _chunk("This scholarship covers full tuition and a monthly stipend.")

    with (
        patch("app.agents.research_qa.agent.retrieve", return_value=[chunk]),
        patch("app.core.llm.generate_text", return_value="I could not confirm this from the official scholarship content."),
    ):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa", headers=headers, json={"question": "Is my spouse covered?"}
        )

    body = response.json()
    assert body["confidence"] == "unknown"
    assert "could not confirm" in body["answer_text"].lower()


def test_scenario_2b_ungrounded_llm_guess_is_downgraded_to_unknown(authed_user) -> None:
    """Defense in depth: even if the LLM answers confidently instead of
    admitting uncertainty, the deterministic ground_check must catch an
    unsupported claim and downgrade it to Unknown rather than passing it
    through as Verified/Inferred."""
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()
    chunk = _chunk("This scholarship covers full tuition and a monthly stipend.")

    with (
        patch("app.agents.research_qa.agent.retrieve", return_value=[chunk]),
        patch("app.core.llm.generate_text", return_value="Yes, spouses and dependents are fully covered under this program."),
    ):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa", headers=headers, json={"question": "Is my spouse covered?"}
        )

    body = response.json()
    assert body["confidence"] == "unknown"


def test_scenario_3_combined_statements_labeled_inferred_not_verified(authed_user) -> None:
    """Given an answer that required combining two related but not identical
    statements on the source page, When the answer is produced, Then it is
    labeled Inferred rather than Verified."""
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()
    chunk = _chunk(
        "Applicants must hold a Bachelor's degree in engineering with at least three years "
        "of relevant industry experience."
    )

    with (
        patch("app.agents.research_qa.agent.retrieve", return_value=[chunk]),
        # Deliberately paraphrased (not verbatim) so lexical overlap with the
        # source lands in the "inferred" band (>=0.3, <0.6), not "verified".
        patch(
            "app.core.llm.generate_text",
            return_value="Preferred applicants typically have engineering degrees and industry experience.",
        ),
    ):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa", headers=headers, json={"question": "What kind of applicant background is preferred?"}
        )

    body = response.json()
    assert body["confidence"] == "inferred"


def test_scenario_4_unrelated_question_not_answered_as_grounded_fact(authed_user) -> None:
    """Given a question unrelated to any scholarship or sourced content
    (general career advice), When asked, Then the system does not answer as
    if it were a grounded scholarship fact — no matching chunks are
    retrieved, so the answer must be Unknown."""
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()

    with patch("app.agents.research_qa.agent.retrieve", return_value=[]):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa",
            headers=headers,
            json={"question": "What career should I pursue in general?"},
        )

    body = response.json()
    assert body["confidence"] == "unknown"
    assert body["source_url"] is None
