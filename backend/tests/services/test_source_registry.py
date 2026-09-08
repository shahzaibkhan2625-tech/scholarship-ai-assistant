"""`source_registry` service tests (T078): thin pass-through over
`source_repo` plus the reusable `is_source_active` gating check. Runs
against the real DB (skip-if-unreachable, mirrors
tests/sources/test_source_registry_compliance.py); every row created is
torn down.
"""

import uuid

import pytest

from app.data.repositories import source_repo
from app.models.source import SourceRegistry
from app.schemas.source import SourceRegistryCreate
from app.services import source_registry


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
        name="Registry Service Test Source",
        source_type="gov",
        official_status="official",
        domain=f"registry-service-test-{uuid.uuid4().hex}.example.com",
        access_method="web",
        reliability_level="high",
    )
    defaults.update(overrides)
    return SourceRegistryCreate(**defaults)


def test_is_source_active_true_for_active_false_for_others(db):
    session, created_sources = db
    active = source_registry.register_source(session, _source_data(status="active"))
    disabled = source_registry.register_source(session, _source_data(status="disabled"))
    created_sources += [active.id, disabled.id]

    assert source_registry.is_source_active(session, active.id) is True
    assert source_registry.is_source_active(session, disabled.id) is False


def test_list_active_sources_delegates_to_repo_gating(db):
    session, created_sources = db
    active = source_registry.register_source(session, _source_data(status="active"))
    failing = source_registry.register_source(session, _source_data(status="failing"))
    created_sources += [active.id, failing.id]

    active_ids = {s.id for s in source_registry.list_active_sources(session)}

    assert active.id in active_ids
    assert failing.id not in active_ids


def test_get_active_source_returns_none_for_unknown_id(db):
    session, _ = db
    assert source_registry.get_active_source(session, uuid.uuid4()) is None
