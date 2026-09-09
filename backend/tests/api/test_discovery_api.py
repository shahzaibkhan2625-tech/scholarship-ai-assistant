"""Contract tests: POST /discovery (T090) and its app wiring (T092).

The Discovery Agent's actual multi-source execution is mocked here — this is
an API-contract test, not a re-test of T089's own guardrail/coverage tests
(tests/agents/test_discovery_agent.py, tests/agents/test_discovery_guardrails.py)."""

from datetime import datetime, timezone
from unittest.mock import patch

from app.agents.discovery.agent import DiscoveryRunResult
from app.models.scholarship import VerificationStatus
from app.schemas.discovery import CoverageSummary, DiscoveryResult, DiscoveryResultItem


def _fake_run_result(*, with_candidate: bool) -> DiscoveryRunResult:
    coverage = CoverageSummary(
        as_of=datetime.now(timezone.utc),
        sources_configured=2,
        sources_active=1,
        sources_checked=1,
        sources_failed=0,
        countries_covered=["Germany"],
        gaps=["Pakistan / gov: no active source configured"],
    )
    results = []
    if with_candidate:
        import uuid

        results = [
            DiscoveryResultItem(
                scholarship_id=uuid.uuid4(),
                source_id=uuid.uuid4(),
                source_type="gov",
                verification_status=VerificationStatus.VERIFIED,
            )
        ]
    return DiscoveryRunResult(
        query_plan=[],
        discovery_result=DiscoveryResult(results=results, coverage=coverage),
        fetch_errors=[],
    )


def test_discovery_requires_auth(authed_user) -> None:
    client = authed_user["client"]

    response = client.post("/discovery")

    assert response.status_code == 401


def test_discovery_without_profile_returns_clear_4xx(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.post("/discovery", headers=headers)

    assert 400 <= response.status_code < 500
    assert "profile" in response.json()["detail"].lower()


def test_discovery_with_profile_returns_candidates_and_coverage(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    client.put(
        "/profile",
        headers=headers,
        json={"nationality": "Pakistani", "target_degree_level": "MS", "target_countries": ["Germany"]},
    )

    with patch("app.api.discovery.run_discovery", return_value=_fake_run_result(with_candidate=True)):
        response = client.post("/discovery", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert "results" in body
    assert len(body["results"]) == 1
    assert "coverage" in body
    assert body["coverage"]["claims_complete_coverage"] is False
    assert body["coverage"]["gaps"]


def test_discovery_with_no_candidates_still_returns_explicit_empty_array_and_coverage(authed_user) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    client.put("/profile", headers=headers, json={"nationality": "Pakistani"})

    with patch("app.api.discovery.run_discovery", return_value=_fake_run_result(with_candidate=False)):
        response = client.post("/discovery", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["coverage"] is not None


def test_discovery_route_registered_in_app() -> None:
    """Proves presence (mirrors the "no direct POST /sources" absence test
    in tests/sources/test_source_registry_compliance.py). This FastAPI
    version wraps each `include_router` call in an opaque `_IncludedRouter`
    on `app.routes` rather than exposing its routes' full paths directly, so
    the prefix from `include_context` is joined with each sub-route's path
    before comparing."""
    from app.main import app

    matches = []
    for route in app.routes:
        prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
        sub_routes = getattr(getattr(route, "original_router", None), "routes", None) or [route]
        for sub in sub_routes:
            full_path = f"{prefix}{getattr(sub, 'path', '') or ''}"
            if full_path == "/discovery" and "POST" in (getattr(sub, "methods", None) or set()):
                matches.append(sub)

    assert len(matches) == 1
