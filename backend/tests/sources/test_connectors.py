"""Connector governance + retry/failure-logging tests (T073, Blueprint §11,
§33; PRD B3 Hard-Gated Zone). Runs against the real DB (skip-if-unreachable,
same pattern as test_source_registry_compliance.py); every row created is
torn down. All network calls are mocked — no live HTTP requests are made.
"""

import uuid

import pytest

from app.data.repositories import source_repo
from app.data.repositories.source_repo import SourceNotApprovedError
from app.models.source import SourceFetchLog, SourceRegistry
from app.schemas.source import SourceRegistryCreate
from app.sources.connectors.api_connector import ApiRawResponse, fetch_api
from app.sources.connectors.official_fetch import fetch_official
from app.tools.web_fetch import FetchResult


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_logs: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_logs
    finally:
        for log_id in created_logs:
            row = session.get(SourceFetchLog, log_id)
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
        name="Test Connector Source",
        source_type="gov",
        official_status="official",
        domain=f"connector-test-{uuid.uuid4().hex}.example.com",
        access_method="web",
        reliability_level="high",
    )
    defaults.update(overrides)
    return SourceRegistryCreate(**defaults)


def _no_sleep(_delay: float) -> None:
    return None


# --- official_fetch connector -------------------------------------------------


def test_official_fetch_rejects_pending_source_with_zero_network_calls(db):
    session, created_sources, _ = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="pending"))
    created_sources.append(source.id)

    calls = {"count": 0}

    def fake_fetch(url: str) -> FetchResult:
        calls["count"] += 1
        return FetchResult(url=url, success=True, status_code=200, html="<html/>")

    with pytest.raises(SourceNotApprovedError):
        fetch_official(session, source.id, "https://example.com/page", fetch=fake_fetch)

    assert calls["count"] == 0


def test_official_fetch_rejects_disabled_source_with_zero_network_calls(db):
    session, created_sources, _ = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="disabled"))
    created_sources.append(source.id)

    calls = {"count": 0}

    def fake_fetch(url: str) -> FetchResult:
        calls["count"] += 1
        return FetchResult(url=url, success=True, status_code=200, html="<html/>")

    with pytest.raises(SourceNotApprovedError):
        fetch_official(session, source.id, "https://example.com/page", fetch=fake_fetch)

    assert calls["count"] == 0


def test_official_fetch_active_source_logs_ok_on_success(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=True, status_code=200, html="<html>content</html>")

    result = fetch_official(session, source.id, "https://example.com/page", fetch=fake_fetch)

    assert result.success is True
    assert result.content == "<html>content</html>"

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]
    assert any(log.status == "ok" for log in logs)


def test_official_fetch_exhausts_retries_logs_fail_and_marks_source_failing(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    calls = {"count": 0}

    def always_failing_fetch(url: str) -> FetchResult:
        calls["count"] += 1
        return FetchResult(url=url, success=False, status_code=503, error="upstream unavailable")

    result = fetch_official(
        session,
        source.id,
        "https://example.com/page",
        max_attempts=3,
        fetch=always_failing_fetch,
        sleep=_no_sleep,
    )

    assert result.success is False
    assert calls["count"] == 3  # bounded retry policy: exactly max_attempts tries

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]
    assert any(log.status == "fail" and log.error == "upstream unavailable" for log in logs)

    session.refresh(source)
    assert source.status == "failing"


# --- api_connector connector --------------------------------------------------


def test_api_connector_rejects_pending_source_with_zero_network_calls(db):
    session, created_sources, _ = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="pending", access_method="api"))
    created_sources.append(source.id)

    calls = {"count": 0}

    def fake_client(_source, _query) -> ApiRawResponse:
        calls["count"] += 1
        return ApiRawResponse(success=True, status_code=200, records=[])

    with pytest.raises(SourceNotApprovedError):
        fetch_api(session, source.id, "masters scholarships", client=fake_client)

    assert calls["count"] == 0


def test_api_connector_active_source_logs_ok_on_success(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active", access_method="api"))
    created_sources.append(source.id)

    def fake_client(_source, _query) -> ApiRawResponse:
        return ApiRawResponse(success=True, status_code=200, records=[{"name": "Test Scholarship"}])

    result = fetch_api(session, source.id, "masters scholarships", client=fake_client)

    assert result.success is True
    assert result.records == [{"name": "Test Scholarship"}]

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]
    assert any(log.status == "ok" and log.items_found == 1 for log in logs)


def test_api_connector_exhausts_retries_logs_fail_and_marks_source_failing(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active", access_method="api"))
    created_sources.append(source.id)

    calls = {"count": 0}

    def always_failing_client(_source, _query) -> ApiRawResponse:
        calls["count"] += 1
        return ApiRawResponse(success=False, status_code=500, error="API unavailable")

    result = fetch_api(
        session,
        source.id,
        "masters scholarships",
        max_attempts=3,
        client=always_failing_client,
        sleep=_no_sleep,
    )

    assert result.success is False
    assert calls["count"] == 3

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]
    assert any(log.status == "fail" and log.error == "API unavailable" for log in logs)

    session.refresh(source)
    assert source.status == "failing"


def test_official_fetch_unknown_source_id_raises(db):
    session, *_ = db
    with pytest.raises(SourceNotApprovedError):
        fetch_official(session, uuid.uuid4(), "https://example.com", fetch=lambda url: FetchResult(url=url, success=True))
