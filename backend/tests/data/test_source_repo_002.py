"""T147: 002 additions to `source_repo` / `schemas.source`.

Schema-validation tests (Q2) need no DB. Repo-persistence tests run against
the real DB (skip-if-unreachable via `db_session_factory`) and need migration
(a) applied; every row is rolled back by the fixture's outer transaction.

Locked semantics (Phase A):
- Q1: on re-upsert, the NEW fields (`listing_page_url`, `freshness_window_days`)
  use `model_fields_set` — omitted preserves, explicit None clears; every 001
  field keeps full-replace behavior.
- Q2: `listing_page_url` must be http(s) and its hostname must equal `domain`.
"""

import uuid

import pytest
from pydantic import ValidationError

from app.data.repositories import source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.schemas.source import SourceFetchLogCreate, SourceFetchLogRead, SourceRegistryCreate, SourceRegistryRead


def _domain() -> str:
    return f"repo002-{uuid.uuid4().hex[:12]}.example.com"


def _source(domain: str | None = None, **overrides) -> SourceRegistryCreate:
    defaults = dict(
        name="Repo 002 Test Source",
        source_type="gov",
        official_status="official",
        domain=domain or _domain(),
        access_method="web",
        reliability_level="high",
    )
    defaults.update(overrides)
    return SourceRegistryCreate(**defaults)


# --- schema (no DB) ----------------------------------------------------------


def test_registry_create_new_fields_default_to_none_and_are_unset():
    data = _source()
    assert data.listing_page_url is None and data.freshness_window_days is None
    assert "listing_page_url" not in data.model_fields_set
    assert "freshness_window_days" not in data.model_fields_set


def test_registry_create_accepts_same_host_listing_url():
    data = _source(domain="www2.daad.de", listing_page_url="https://www2.daad.de/deutschland/stipendium/en/")
    assert data.listing_page_url.startswith("https://www2.daad.de/")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www2.daad.de/x",
        "javascript:alert(1)",
        "//www2.daad.de/x",
        "www2.daad.de/x",
        "https://evil.example.org/x",
        "https://www2.daad.de.evil.example.org/x",
        "https://sub.www2.daad.de/x",
    ],
)
def test_registry_create_rejects_bad_listing_url(url):
    with pytest.raises(ValidationError):
        _source(domain="www2.daad.de", listing_page_url=url)


def test_registry_create_hostname_compare_is_case_insensitive():
    data = _source(domain="www2.daad.de", listing_page_url="HTTPS://WWW2.DAAD.DE/x")
    assert data.listing_page_url


def test_registry_create_none_listing_url_is_valid():
    assert _source(listing_page_url=None).listing_page_url is None


def test_fetch_log_create_defaults():
    data = SourceFetchLogCreate(source_id=uuid.uuid4(), status="ok")
    assert data.fetched_url is None
    assert data.used_homepage_fallback is False
    assert data.monitoring_run_id is None


def test_read_schemas_expose_new_fields():
    assert {"listing_page_url", "freshness_window_days"} <= set(SourceRegistryRead.model_fields)
    assert {"fetched_url", "used_homepage_fallback", "monitoring_run_id"} <= set(SourceFetchLogRead.model_fields)


# --- repo persistence (DB) ---------------------------------------------------


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def test_upsert_persists_new_fields(db):
    domain = _domain()
    row = source_repo.upsert_source_by_domain(
        db, _source(domain, listing_page_url=f"https://{domain}/list", freshness_window_days=14)
    )
    db.expire_all()
    fetched = source_repo.get_source_by_domain(db, domain)
    assert fetched.id == row.id
    assert fetched.listing_page_url == f"https://{domain}/list"
    assert fetched.freshness_window_days == 14


def test_upsert_new_fields_default_to_null_on_insert(db):
    row = source_repo.upsert_source_by_domain(db, _source())
    assert row.listing_page_url is None and row.freshness_window_days is None


def test_reupsert_with_different_listing_url_replaces_it(db):
    domain = _domain()
    source_repo.upsert_source_by_domain(db, _source(domain, listing_page_url=f"https://{domain}/a"))
    row = source_repo.upsert_source_by_domain(db, _source(domain, listing_page_url=f"https://{domain}/b"))
    assert row.listing_page_url == f"https://{domain}/b"


def test_reupsert_omitting_new_fields_preserves_existing_values(db):
    domain = _domain()
    source_repo.upsert_source_by_domain(
        db, _source(domain, listing_page_url=f"https://{domain}/a", freshness_window_days=30)
    )
    row = source_repo.upsert_source_by_domain(db, _source(domain, name="Renamed"))
    assert row.name == "Renamed"
    assert row.listing_page_url == f"https://{domain}/a"
    assert row.freshness_window_days == 30


def test_reupsert_with_explicit_none_clears_new_fields(db):
    domain = _domain()
    source_repo.upsert_source_by_domain(
        db, _source(domain, listing_page_url=f"https://{domain}/a", freshness_window_days=30)
    )
    row = source_repo.upsert_source_by_domain(db, _source(domain, listing_page_url=None, freshness_window_days=None))
    assert row.listing_page_url is None
    assert row.freshness_window_days is None


def test_reupsert_001_fields_keep_full_replace_behavior(db):
    domain = _domain()
    source_repo.upsert_source_by_domain(db, _source(domain, notes="old note", organization="Org"))
    row = source_repo.upsert_source_by_domain(db, _source(domain))  # notes/organization omitted
    assert row.notes is None and row.organization is None


def test_log_fetch_persists_new_fields(db):
    source = source_repo.upsert_source_by_domain(db, _source())
    run = MonitoringRun(trigger=MonitoringTrigger.MANUAL)
    db.add(run)
    db.commit()
    db.refresh(run)

    log = source_repo.log_fetch(
        db,
        SourceFetchLogCreate(
            source_id=source.id,
            status="ok",
            fetched_url="https://example.com/list",
            used_homepage_fallback=True,
            monitoring_run_id=run.id,
        ),
    )
    db.expire_all()
    stored = source_repo.get_fetch_logs_for_source(db, source.id)[0]
    assert stored.id == log.id
    assert stored.fetched_url == "https://example.com/list"
    assert stored.used_homepage_fallback is True
    assert stored.monitoring_run_id == run.id


def test_log_fetch_defaults_when_new_fields_omitted(db):
    source = source_repo.upsert_source_by_domain(db, _source())
    source_repo.log_fetch(db, SourceFetchLogCreate(source_id=source.id, status="fail", error="boom"))
    db.expire_all()
    stored = source_repo.get_fetch_logs_for_source(db, source.id)[0]
    assert stored.fetched_url is None
    assert stored.used_homepage_fallback is False
    assert stored.monitoring_run_id is None
