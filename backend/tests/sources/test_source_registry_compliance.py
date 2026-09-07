"""Source-registry compliance tests (T066, constitution Principle IV):
- a non-`active` `source_id` is never returned as authoritative,
- a `candidate_sources` row is never joined as authoritative before it is
  `approved`,
- a failed fetch is recorded, never silently dropped (§33).

Runs against the real DB (mirrors tests/conftest.py's skip-if-unreachable
pattern via `db_session_factory`); every row a test creates is deleted in a
teardown block so the shared dev database isn't left polluted.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.data.repositories import source_repo
from app.data.repositories.source_repo import (
    CandidateSourceAlreadyReviewedError,
    CandidateSourceNotFoundError,
)
from app.models.source import CandidateSource, SourceFetchLog, SourceRegistry
from app.schemas.source import CandidateSourceCreate, SourceFetchLogCreate, SourceRegistryCreate
from app.services.source_registry_seed import load_seed_rows, seed_sources


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_candidates: list[uuid.UUID] = []
    created_logs: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_candidates, created_logs
    finally:
        for log_id in created_logs:
            row = session.get(SourceFetchLog, log_id)
            if row is not None:
                session.delete(row)
        for candidate_id in created_candidates:
            row = session.get(CandidateSource, candidate_id)
            if row is not None:
                session.delete(row)
        for source_id in created_sources:
            row = session.get(SourceRegistry, source_id)
            if row is not None:
                session.delete(row)
        session.commit()
        session.close()


def _source_data(**overrides) -> SourceRegistryCreate:
    defaults = dict(
        name="Test Source",
        source_type="gov",
        official_status="official",
        domain=f"test-{uuid.uuid4().hex}.example.com",
        access_method="web",
        reliability_level="high",
    )
    defaults.update(overrides)
    return SourceRegistryCreate(**defaults)


def test_seeding_twice_produces_exactly_five_rows(db_session_factory):
    seed_sources()
    seed_sources()

    rows = load_seed_rows()
    with db_session_factory() as db:
        found = [source_repo.get_source_by_domain(db, row.domain) for row in rows]

    assert len(rows) == 5
    assert all(source is not None for source in found)
    assert len({source.id for source in found}) == 5


def test_get_active_sources_excludes_failing_and_disabled(db):
    session, created_sources, _, _ = db

    active = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    disabled = source_repo.upsert_source_by_domain(session, _source_data(status="disabled"))
    failing = source_repo.upsert_source_by_domain(session, _source_data(status="failing"))
    created_sources += [active.id, disabled.id, failing.id]

    active_ids = {source.id for source in source_repo.get_active_sources(session)}

    assert active.id in active_ids
    assert disabled.id not in active_ids
    assert failing.id not in active_ids


def test_pending_candidate_source_is_not_authoritative(db):
    session, created_sources, created_candidates, _ = db

    candidate = source_repo.create_candidate_source(
        session,
        CandidateSourceCreate(url="https://newly-found.example.com/scholarships", proposed_type="gov"),
    )
    created_candidates.append(candidate.id)

    assert candidate.status == "pending"

    # A pending candidate is never returned by get_active_sources...
    active_ids = {source.id for source in source_repo.get_active_sources(session)}
    assert candidate.id not in active_ids

    # ...and its id resolves to nothing in source_registry (table separation,
    # not a status flag, is what makes it non-authoritative).
    assert source_repo.get_source_by_id(session, candidate.id) is None


def test_approve_candidate_source_promotes_it_to_active(authed_user, db):
    session, created_sources, created_candidates, _ = db

    candidate = source_repo.create_candidate_source(
        session,
        CandidateSourceCreate(url="https://approved-example.example.com/scholarships", proposed_type="gov"),
    )
    created_candidates.append(candidate.id)
    reviewer_id = authed_user["user_id"]

    source = source_repo.approve_candidate_source(
        session,
        candidate.id,
        reviewer_id,
        _source_data(name="Approved Example", status="active"),
    )
    created_sources.append(source.id)

    assert source.status == "active"
    assert source_repo.get_source_by_id(session, source.id) is not None
    active_ids = {s.id for s in source_repo.get_active_sources(session)}
    assert source.id in active_ids

    refreshed_candidate = source_repo.get_candidate_source_by_id(session, candidate.id)
    assert refreshed_candidate.status == "approved"
    assert refreshed_candidate.reviewed_by == reviewer_id
    assert refreshed_candidate.reviewed_at is not None


def test_approve_already_reviewed_candidate_raises(authed_user, db):
    session, created_sources, created_candidates, _ = db

    candidate = source_repo.create_candidate_source(
        session, CandidateSourceCreate(url="https://double-approve.example.com")
    )
    created_candidates.append(candidate.id)
    reviewer_id = authed_user["user_id"]

    source = source_repo.approve_candidate_source(session, candidate.id, reviewer_id, _source_data())
    created_sources.append(source.id)

    with pytest.raises(CandidateSourceAlreadyReviewedError):
        source_repo.approve_candidate_source(session, candidate.id, reviewer_id, _source_data())


def test_approve_unknown_candidate_raises(db):
    session, *_ = db

    with pytest.raises(CandidateSourceNotFoundError):
        source_repo.approve_candidate_source(session, uuid.uuid4(), uuid.uuid4(), _source_data())


def test_reject_candidate_source_records_reason(authed_user, db):
    session, _, created_candidates, _ = db

    candidate = source_repo.create_candidate_source(
        session, CandidateSourceCreate(url="https://reject-me.example.com")
    )
    created_candidates.append(candidate.id)
    reviewer_id = authed_user["user_id"]

    rejected = source_repo.reject_candidate_source(session, candidate.id, reviewer_id, "not an official source")

    assert rejected.status == "rejected"
    assert rejected.reviewed_by == reviewer_id
    assert rejected.signals["rejection_reason"] == "not an official source"
    assert source_repo.get_source_by_id(session, candidate.id) is None


def test_log_fetch_records_failure_not_silently_dropped(db):
    session, created_sources, _, created_logs = db

    source = source_repo.upsert_source_by_domain(session, _source_data())
    created_sources.append(source.id)

    log = source_repo.log_fetch(
        session,
        SourceFetchLogCreate(source_id=source.id, status="fail", http_status=503, error="upstream unavailable"),
    )
    created_logs.append(log.id)

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    assert any(row.id == log.id and row.status == "fail" and row.error == "upstream unavailable" for row in logs)


def test_no_direct_post_sources_route_exists():
    from app.main import app

    offending = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/sources" and "POST" in getattr(route, "methods", set())
    ]
    assert offending == []


def test_domain_schema_rejects_scheme():
    with pytest.raises(ValidationError):
        _source_data(domain="https://example.com")


def test_domain_schema_rejects_path():
    with pytest.raises(ValidationError):
        _source_data(domain="example.com/scholarships")
