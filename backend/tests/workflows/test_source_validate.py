"""`source_validate` workflow tests (T086, Blueprint §10, §29): a candidate
cannot reach 'approved' without the human-approval-gate checkpoint being
explicitly satisfied. Runs against the real DB (skip-if-unreachable, mirrors
tests/sources/test_connectors.py); every row created is torn down. All
network calls are mocked.
"""

import uuid
from unittest.mock import patch

import pytest

from app.data.repositories import source_repo
from app.models.source import CandidateSource, SourceRegistry
from app.schemas.source import CandidateSourceCreate, SourceRegistryCreate
from app.tools.web_fetch import FetchResult
from app.workflows.source_validate.graph import run_source_validate


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_candidates: list[uuid.UUID] = []
    created_sources: list[uuid.UUID] = []
    try:
        yield session, created_candidates, created_sources
    finally:
        for source_id in created_sources:
            row = session.get(SourceRegistry, source_id)
            if row is not None:
                session.delete(row)
        for candidate_id in created_candidates:
            row = session.get(CandidateSource, candidate_id)
            if row is not None:
                session.delete(row)
        session.commit()
        session.close()


def _candidate(session, created_candidates):
    candidate = source_repo.create_candidate_source(
        session,
        CandidateSourceCreate(url=f"https://gate-test-{uuid.uuid4().hex}.example.com/scholarships", proposed_type="gov"),
    )
    created_candidates.append(candidate.id)
    return candidate


def test_gate_not_approved_pauses_and_never_promotes(db):
    session, created_candidates, created_sources = db
    candidate = _candidate(session, created_candidates)

    with (
        patch(
            "app.workflows.source_validate.graph.fetch_url",
            return_value=FetchResult(url=candidate.url, success=True, status_code=200, html="<html/>"),
        ),
        patch("app.data.repositories.source_repo.approve_candidate_source") as mock_approve,
    ):
        result = run_source_validate(session, candidate.id, candidate.url, approved=False)

    assert result["status"] == "pending_approval"
    assert "approved_source" not in result or result.get("approved_source") is None
    mock_approve.assert_not_called()

    refreshed = source_repo.get_candidate_source_by_id(session, candidate.id)
    assert refreshed.status == "pending"


def test_gate_approved_promotes_to_active_source(authed_user, db):
    session, created_candidates, created_sources = db
    candidate = _candidate(session, created_candidates)
    reviewer_id = authed_user["user_id"]

    source_data = SourceRegistryCreate(
        name="Gate-Approved Source",
        source_type="gov",
        official_status="official",
        domain=f"gate-approved-{uuid.uuid4().hex}.example.com",
        access_method="web",
        reliability_level="high",
    )

    with patch(
        "app.workflows.source_validate.graph.fetch_url",
        return_value=FetchResult(url=candidate.url, success=True, status_code=200, html="<html/>"),
    ):
        result = run_source_validate(
            session,
            candidate.id,
            candidate.url,
            approved=True,
            reviewed_by=reviewer_id,
            source_data=source_data,
        )

    assert result["status"] == "promoted"
    approved_source = result["approved_source"]
    created_sources.append(approved_source.id)
    assert approved_source.status == "active"

    refreshed = source_repo.get_candidate_source_by_id(session, candidate.id)
    assert refreshed.status == "approved"
    assert refreshed.reviewed_by == reviewer_id
