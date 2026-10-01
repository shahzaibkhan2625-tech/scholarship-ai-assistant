"""`record_or_bump_candidate_source` tests (US4 Acceptance Scenario 3,
ADR-0005). Runs against the real DB (skip-if-unreachable, mirrors
`test_source_registry_compliance.py`); every row a test creates is deleted
in a teardown block so the shared dev database isn't left polluted.
"""

import uuid

import pytest

from app.data.repositories import source_repo
from app.models.source import CandidateSource, SourceRegistry
from app.schemas.source import CandidateSourceCreate, SourceRegistryCreate


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_candidates: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_candidates
    finally:
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


def test_new_domain_creates_a_pending_candidate_with_seen_count_one(db):
    session, _, created_candidates = db
    domain = f"brand-new-{uuid.uuid4().hex}.example.org"

    candidate = source_repo.record_or_bump_candidate_source(
        session,
        domain=domain,
        url=f"https://{domain}/apply",
        discovered_from="https://www2.daad.de/en/scholarships/",
        matched_keyword="scholarship",
    )
    created_candidates.append(candidate.id)

    assert candidate.status == "pending"
    assert candidate.signals["seen_count"] == 1
    assert candidate.signals["matched_keyword"] == "scholarship"
    assert candidate.signals["discovered_from_urls"] == ["https://www2.daad.de/en/scholarships/"]


def test_domain_already_active_in_registry_is_a_noop(db):
    session, created_sources, created_candidates = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    result = source_repo.record_or_bump_candidate_source(
        session,
        domain=source.domain,
        url=f"https://{source.domain}/apply",
        discovered_from=None,
    )

    assert result is None
    matching = [
        c
        for c in source_repo.list_candidate_sources(session, status=None)
        if source_repo.domain_from_url(c.url) == source.domain
    ]
    created_candidates.extend(c.id for c in matching)
    assert matching == []


def test_domain_already_disabled_in_registry_is_still_a_noop(db):
    """`get_source_by_domain` has no status filter — a domain already known
    to the registry (even disabled/failing) must never be re-proposed."""
    session, created_sources, _ = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="disabled"))
    created_sources.append(source.id)

    result = source_repo.record_or_bump_candidate_source(
        session, domain=source.domain, url=f"https://{source.domain}/apply", discovered_from=None
    )

    assert result is None


def test_repeated_detection_of_same_domain_bumps_not_duplicates(db):
    session, _, created_candidates = db
    domain = f"repeat-me-{uuid.uuid4().hex}.example.org"
    url = f"https://{domain}/apply"

    first = source_repo.record_or_bump_candidate_source(
        session, domain=domain, url=url, discovered_from="https://source-a.example.com/list", matched_keyword="grant"
    )
    created_candidates.append(first.id)

    second = source_repo.record_or_bump_candidate_source(
        session, domain=domain, url=url, discovered_from="https://source-b.example.com/list", matched_keyword="grant"
    )

    assert second.id == first.id
    assert second.signals["seen_count"] == 2
    assert set(second.signals["discovered_from_urls"]) == {
        "https://source-a.example.com/list",
        "https://source-b.example.com/list",
    }

    matching = [
        c
        for c in source_repo.list_candidate_sources(session, status=None)
        if source_repo.domain_from_url(c.url) == domain
    ]
    assert len(matching) == 1


def test_same_discovered_from_url_is_not_duplicated_in_signals(db):
    session, _, created_candidates = db
    domain = f"same-source-{uuid.uuid4().hex}.example.org"
    url = f"https://{domain}/apply"

    first = source_repo.record_or_bump_candidate_source(
        session, domain=domain, url=url, discovered_from="https://source-a.example.com/list"
    )
    created_candidates.append(first.id)

    second = source_repo.record_or_bump_candidate_source(
        session, domain=domain, url=url, discovered_from="https://source-a.example.com/list"
    )

    assert second.signals["discovered_from_urls"] == ["https://source-a.example.com/list"]
    assert second.signals["seen_count"] == 2


def test_already_approved_candidate_domain_is_a_noop(authed_user, db):
    session, created_sources, created_candidates = db
    domain = f"already-approved-{uuid.uuid4().hex}.example.org"
    url = f"https://{domain}/apply"

    candidate = source_repo.create_candidate_source(session, CandidateSourceCreate(url=url))
    created_candidates.append(candidate.id)

    reviewer_id = authed_user["user_id"]
    approved_source = source_repo.approve_candidate_source(
        session, candidate.id, reviewer_id, _source_data(name="Approved", domain=domain, status="active")
    )
    created_sources.append(approved_source.id)

    result = source_repo.record_or_bump_candidate_source(session, domain=domain, url=url, discovered_from=None)

    assert result is None


def test_already_rejected_candidate_domain_is_a_noop_and_no_new_row_created(authed_user, db):
    session, _, created_candidates = db
    domain = f"already-rejected-{uuid.uuid4().hex}.example.org"
    url = f"https://{domain}/apply"

    candidate = source_repo.create_candidate_source(session, CandidateSourceCreate(url=url))
    created_candidates.append(candidate.id)
    source_repo.reject_candidate_source(session, candidate.id, authed_user["user_id"], "not an official source")

    result = source_repo.record_or_bump_candidate_source(session, domain=domain, url=url, discovered_from=None)

    assert result is None
    matching = [
        c
        for c in source_repo.list_candidate_sources(session, status=None)
        if source_repo.domain_from_url(c.url) == domain
    ]
    assert len(matching) == 1  # only the original rejected row, no new one
