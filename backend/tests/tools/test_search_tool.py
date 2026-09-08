"""`search` tool tests (T075, FR-DISC-2) — proves the domain-scoping gate is
real filtering behavior, not just documentation: a raw search backend that
returns hits from unapproved domains must never leak them past this tool.
Runs against the real DB (skip-if-unreachable, same pattern as
tests/sources/test_source_registry_compliance.py); rows created are torn
down. No live network search is performed — `raw_search` is a fake.
"""

import uuid

import pytest

from app.data.repositories import source_repo
from app.models.source import SourceRegistry
from app.schemas.source import SourceRegistryCreate
from app.tools.search import RawSearchHit, search_tool


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    try:
        yield session, created_sources
    finally:
        for source_id in created_sources:
            row = session.get(SourceRegistry, source_id)
            if row is not None:
                session.delete(row)
        session.commit()
        session.close()


def _source_data(**overrides) -> SourceRegistryCreate:
    defaults = dict(
        name="Test Search Source",
        source_type="gov",
        official_status="official",
        domain=f"search-test-{uuid.uuid4().hex}.example.com",
        access_method="web",
        reliability_level="high",
    )
    defaults.update(overrides)
    return SourceRegistryCreate(**defaults)


def test_search_tool_keeps_hits_from_active_registered_domains(db):
    session, created_sources = db
    approved = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(approved.id)

    def fake_raw_search(_query: str) -> list[RawSearchHit]:
        return [
            RawSearchHit(
                title="Official scholarship page",
                url=f"https://{approved.domain}/scholarships",
                snippet="...",
            )
        ]

    results = search_tool(session, "masters scholarships", raw_search=fake_raw_search)

    assert len(results) == 1
    assert results[0].url == f"https://{approved.domain}/scholarships"
    assert results[0].source_id == str(approved.id)


def test_search_tool_rejects_hits_from_domains_not_in_registry(db):
    session, created_sources = db
    approved = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(approved.id)

    def fake_raw_search(_query: str) -> list[RawSearchHit]:
        return [
            RawSearchHit(title="Approved hit", url=f"https://{approved.domain}/page", snippet="..."),
            RawSearchHit(title="Random blog", url="https://unapproved-blog.example.org/post", snippet="..."),
        ]

    results = search_tool(session, "masters scholarships", raw_search=fake_raw_search)

    assert len(results) == 1
    assert results[0].url == f"https://{approved.domain}/page"


def test_search_tool_rejects_hits_from_disabled_or_pending_sources(db):
    session, created_sources = db
    disabled = source_repo.upsert_source_by_domain(session, _source_data(status="disabled"))
    pending = source_repo.upsert_source_by_domain(session, _source_data(status="pending"))
    created_sources += [disabled.id, pending.id]

    def fake_raw_search(_query: str) -> list[RawSearchHit]:
        return [
            RawSearchHit(title="Disabled source hit", url=f"https://{disabled.domain}/page", snippet="..."),
            RawSearchHit(title="Pending source hit", url=f"https://{pending.domain}/page", snippet="..."),
        ]

    results = search_tool(session, "masters scholarships", raw_search=fake_raw_search)

    assert results == []


def test_search_tool_default_raw_search_raises_when_unconfigured(db):
    session, _ = db
    with pytest.raises(NotImplementedError):
        search_tool(session, "masters scholarships")
