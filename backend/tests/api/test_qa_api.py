"""Contract test: POST /scholarships/{scholarship_id}/qa. The retrieve/LLM
boundary is mocked so this test is deterministic and does not depend on live
network/API quota."""

import uuid
from unittest.mock import patch

from app.rag.retrieve import RetrievedChunk


def test_qa_requires_auth(authed_user) -> None:
    client = authed_user["client"]
    scholarship_id = uuid.uuid4()

    response = client.post(f"/scholarships/{scholarship_id}/qa", json={"question": "Does this require GRE?"})

    assert response.status_code == 401


def test_qa_returns_verified_answer_with_citation(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()
    chunk = RetrievedChunk(
        text="This scholarship does not require the GRE. IELTS 6.5 is mandatory.",
        source_url="https://example.test/official-page",
        score=0.9,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )

    with (
        patch("app.agents.research_qa.agent.retrieve", return_value=[chunk]),
        patch("app.core.llm.generate_text", return_value="This scholarship does not require the GRE."),
    ):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa",
            headers=headers,
            json={"question": "Does this require GRE?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] == "verified"
    assert body["source_url"] == "https://example.test/official-page"
    assert body["evidence_snippet"]


def test_qa_returns_unknown_when_nothing_retrieved(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    scholarship_id = uuid.uuid4()

    with patch("app.agents.research_qa.agent.retrieve", return_value=[]):
        response = client.post(
            f"/scholarships/{scholarship_id}/qa",
            headers=headers,
            json={"question": "Is my spouse covered?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["confidence"] == "unknown"
    assert body["source_url"] is None
