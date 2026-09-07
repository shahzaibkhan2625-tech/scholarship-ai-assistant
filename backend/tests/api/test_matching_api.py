"""Contract tests: POST /scholarships/match-url, POST /scholarships/{id}/match,
GET /matches. External calls (fetch, LLM extraction, RAG ingestion) are
mocked at the workflow boundary so these tests are deterministic and do not
depend on live network/API quota."""

from datetime import date, timedelta
from unittest.mock import patch

from app.tools.extract_requirements import ExtractedRequirement, ExtractedScholarshipData
from app.tools.web_fetch import FetchResult

_FUTURE_DEADLINE = (date.today() + timedelta(days=90)).isoformat()


def _extracted_scholarship(name: str = "Test Merit Scholarship") -> ExtractedScholarshipData:
    return ExtractedScholarshipData(
        name=name,
        provider="Test University",
        country="Germany",
        field="Computer Science",
        degree_level="MS",
        funding_status="fully_funded",
        official_application_url="https://example.test/apply",
        deadline=_FUTURE_DEADLINE,
        requirements=[
            ExtractedRequirement(category="gpa", key="min_gpa", value=3.0, mandatory=True, value_status="known", confidence="verified"),
        ],
    )


def _mock_match_url(client, headers, url: str, extracted: ExtractedScholarshipData | None = None):
    extracted = extracted or _extracted_scholarship()
    with (
        patch("app.workflows.url_match.graph.fetch_url", return_value=FetchResult(url=url, success=True, status_code=200, html="<html><body>Scholarship details</body></html>")),
        patch("app.workflows.url_match.graph.extract_requirements_from_page", return_value=extracted),
        patch("app.workflows.url_match.graph.ingest_html_page", return_value=0),
    ):
        return client.post("/scholarships/match-url", headers=headers, json={"url": url})


def test_match_url_returns_verdict_schema(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    client.put("/profile", headers=headers, json={"nationality": "Pakistani"})

    response = _mock_match_url(client, headers, "https://example.test/scholarship-a")

    assert response.status_code == 200
    body = response.json()
    assert "scholarship_id" in body
    assert body["official_source"] == "https://example.test/scholarship-a"
    verdict = body["verdict"]
    assert verdict["eligibility_verdict"] in {"eligible", "likely", "possibly", "not", "unknown_requires_verification"}
    assert verdict["match_strength"] in {"strong", "possible", "not"}
    assert "hard_constraints" in verdict
    assert "soft_preferences" in verdict
    assert "missing_information" in verdict


def test_match_url_fetch_failure_reports_explicitly_not_as_ineligible(authed_user) -> None:
    """US2 Acceptance Scenario 4: an unreachable URL is reported as a fetch
    failure, never as 'not eligible' or a fabricated verdict."""
    client, headers = authed_user["client"], authed_user["headers"]

    with patch(
        "app.workflows.url_match.graph.fetch_url",
        return_value=FetchResult(url="https://example.test/down", success=False, error="Could not reach URL: connection refused"),
    ):
        response = client.post("/scholarships/match-url", headers=headers, json={"url": "https://example.test/down"})

    assert response.status_code == 422
    body = response.json()
    assert body["reported_as"] == "fetch_failure"
    assert "verdict" not in body
    assert "eligible" not in body.get("error", "").lower()


def test_match_stored_scholarship_by_id_returns_verdict(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    match_response = _mock_match_url(client, headers, "https://example.test/scholarship-b")
    scholarship_id = match_response.json()["scholarship_id"]

    response = client.post(f"/scholarships/{scholarship_id}/match", headers=headers)

    assert response.status_code == 200
    assert response.json()["scholarship_id"] == scholarship_id


def test_match_unknown_scholarship_id_returns_404(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.post("/scholarships/00000000-0000-0000-0000-000000000000/match", headers=headers)

    assert response.status_code == 404


def test_list_matches_returns_ranked_array(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    _mock_match_url(client, headers, "https://example.test/scholarship-c")

    response = client.get("/matches", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert "eligibility_verdict" in body[0]
