"""Integration test covering spec.md US2 Acceptance Scenarios 1-4."""

from datetime import date, timedelta
from unittest.mock import patch

from app.tools.extract_requirements import ExtractedFundingDetails, ExtractedRequirement, ExtractedScholarshipData
from app.tools.web_fetch import FetchResult

_FUTURE_DEADLINE = (date.today() + timedelta(days=90)).isoformat()


def _mock_url_match(client, headers, url: str, extracted: ExtractedScholarshipData):
    with (
        patch("app.workflows.url_match.graph.fetch_url", return_value=FetchResult(url=url, success=True, status_code=200, html="<html><body>page</body></html>")),
        patch("app.workflows.url_match.graph.extract_requirements_from_page", return_value=extracted),
        patch("app.workflows.url_match.graph.ingest_html_page", return_value=0),
    ):
        return client.post("/scholarships/match-url", headers=headers, json={"url": url})


def test_scenario_1_full_verdict_with_evidence_and_per_criterion_breakdown(authed_user) -> None:
    """Given a completed profile and a valid official scholarship URL, When
    the user submits the URL for matching, Then the system returns an
    eligibility verdict, a match-strength, and a per-criterion breakdown,
    each backed by evidence from the source page."""
    client, headers = authed_user["client"], authed_user["headers"]
    client.put("/profile", headers=headers, json={"nationality": "Pakistani"})
    client.post(
        "/profile/criteria",
        headers=headers,
        json={"dimension": "gpa", "operator": ">=", "value": None, "kind": "hard_constraint"},
    )
    client.put("/profile", headers=headers, json={"education_records": [{"gpa": 3.8, "gpa_scale": 4.0}]})

    extracted = ExtractedScholarshipData(
        name="Scenario 1 Scholarship",
        degree_level="MS",
        deadline=_FUTURE_DEADLINE,
        requirements=[
            ExtractedRequirement(category="gpa", key="min_gpa", value=3.5, mandatory=True, value_status="known", confidence="verified"),
        ],
    )

    response = _mock_url_match(client, headers, "https://example.test/scenario-1", extracted)

    assert response.status_code == 200
    verdict = response.json()["verdict"]
    assert verdict["eligibility_verdict"] in {"eligible", "likely", "possibly", "not", "unknown_requires_verification"}
    assert verdict["match_strength"] in {"strong", "possible", "not"}
    gpa_outcome = next(c for c in verdict["hard_constraints"] if c["criterion"] == "gpa")
    assert gpa_outcome["result"] == "pass"
    assert gpa_outcome["evidence"]  # every pass/fail is backed by evidence


def test_scenario_2_hard_fail_overrides_strong_soft_alignment(authed_user) -> None:
    """Given a profile that fails a hard constraint (wrong nationality),
    When matching runs, Then the verdict is 'Not' regardless of soft
    alignment, and the failing hard constraint is clearly identified."""
    client, headers = authed_user["client"], authed_user["headers"]
    client.put("/profile", headers=headers, json={"nationality": "Wronglandian"})
    client.post(
        "/profile/criteria",
        headers=headers,
        json={"dimension": "nationality", "operator": "=", "value": "Wronglandian", "kind": "hard_constraint"},
    )

    extracted = ExtractedScholarshipData(
        name="Scenario 2 Scholarship",
        deadline=_FUTURE_DEADLINE,
        requirements=[
            ExtractedRequirement(category="nationality", key="eligible_nationalities", value=["Pakistani"], mandatory=True, value_status="known", confidence="verified"),
        ],
    )

    response = _mock_url_match(client, headers, "https://example.test/scenario-2", extracted)

    verdict = response.json()["verdict"]
    assert verdict["eligibility_verdict"] == "not"
    nationality_outcome = next(c for c in verdict["hard_constraints"] if c["criterion"] == "nationality")
    assert nationality_outcome["result"] == "fail"
    assert "nationality" in verdict["failed_criteria"]


def test_scenario_3_unstated_criterion_is_unknown_not_assumed(authed_user) -> None:
    """Given a scholarship page missing a specific data point (no stated GRE
    requirement), When matching runs, Then that criterion is marked Unknown
    rather than assumed pass or fail."""
    client, headers = authed_user["client"], authed_user["headers"]
    client.post(
        "/profile/criteria",
        headers=headers,
        json={"dimension": "test", "operator": "=", "value": None, "kind": "hard_constraint"},
    )

    extracted = ExtractedScholarshipData(name="Scenario 3 Scholarship", deadline=_FUTURE_DEADLINE, requirements=[])

    response = _mock_url_match(client, headers, "https://example.test/scenario-3", extracted)

    verdict = response.json()["verdict"]
    test_outcome = next(c for c in verdict["hard_constraints"] if c["criterion"] == "test")
    assert test_outcome["result"] == "unknown"
    assert "test" in verdict["unverified_criteria"]


def test_scenario_4_unreachable_url_reports_fetch_failure_explicitly(authed_user) -> None:
    """Given a URL that cannot be fetched, When the user submits it, Then
    the system reports the fetch failure explicitly and does not report
    'not eligible' or fabricate a verdict."""
    client, headers = authed_user["client"], authed_user["headers"]

    with patch(
        "app.workflows.url_match.graph.fetch_url",
        return_value=FetchResult(url="https://example.test/gone", success=False, status_code=404, error="Server responded with HTTP 404"),
    ):
        response = client.post("/scholarships/match-url", headers=headers, json={"url": "https://example.test/gone"})

    assert response.status_code == 422
    body = response.json()
    assert body["reported_as"] == "fetch_failure"
    assert "404" in body["error"] or "HTTP" in body["error"]
