"""Connector governance + retry/failure-logging tests (T073, Blueprint §11,
§33; PRD B3 Hard-Gated Zone). Runs against the real DB (skip-if-unreachable,
same pattern as test_source_registry_compliance.py); every row created is
torn down. All network calls are mocked — no live HTTP requests are made.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import source_repo
from app.data.repositories.source_repo import SourceNotApprovedError
from app.models.scholarship import Scholarship
from app.models.source import FetchStatus, ScholarshipSource, SourceFetchLog, SourceRegistry, VerificationStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.sources.connectors.api_connector import ApiRawResponse, fetch_api
from app.sources.connectors.official_fetch import fetch_and_extract_listing, fetch_official
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
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


# --- fetch_and_extract_listing (T135) -----------------------------------------


def test_listing_extraction_active_source_logs_items_found_and_returns_candidates(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>")

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Connector Test Scholarship")],
            accepted_count=1,
            rejected_count=0,
        )

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )

    assert result.status == "ok"
    assert result.candidates == [{"name": "Connector Test Scholarship", "funding_status": "unknown"}]

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]
    assert any(log.status == "ok" and log.items_found == 1 for log in logs)

    session.refresh(source)
    assert source.status == "active"  # success never flips status


def test_listing_extraction_zero_accepted_candidates_logs_fail_and_marks_source_failing(db):
    """T135/A5+A8: a successful fetch that yields zero GROUNDED candidates
    must not look like a quiet "no scholarships found" -- it's logged and the
    source marked failing, exactly like a real fetch failure."""
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=True, status_code=200, html="<html>nothing here</html>")

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )

    assert result.status == "fail"
    assert "0 candidates" in result.error

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]
    assert any(log.status == "fail" for log in logs)

    session.refresh(source)
    assert source.status == "failing"


def test_listing_extraction_fetch_failure_never_calls_extraction(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    def always_failing_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=False, status_code=503, error="upstream unavailable")

    extract_calls = {"count": 0}

    def counting_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        extract_calls["count"] += 1
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    result = fetch_and_extract_listing(
        session,
        source.id,
        "https://example.com/list",
        max_attempts=1,
        fetch=always_failing_fetch,
        extract_listing=counting_extract_listing,
        sleep=_no_sleep,
    )

    assert result.status == "fail"
    assert extract_calls["count"] == 0  # never spend an LLM call after a raw fetch failure

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]


def test_listing_extraction_reuses_cache_within_ttl_without_refetching(db):
    """T135/A9 layer 2: a prior successful extraction still within its
    update_frequency-derived TTL means neither the fetch NOR the LLM
    extraction runs again -- the already-persisted scholarship_sources rows
    are reused instead."""
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active", update_frequency="weekly"))
    created_sources.append(source.id)

    prior_log = source_repo.log_fetch(
        session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK, items_found=1, retry_count=0)
    )
    created_logs.append(prior_log.id)

    scholarship = Scholarship(name="Cached Scholarship", official_scholarship_url="https://example.com/list")
    session.add(scholarship)
    session.commit()
    scholarship_source = ScholarshipSource(
        scholarship_id=scholarship.id,
        source_id=source.id,
        url="https://example.com/list",
        verification_status=VerificationStatus.UNVERIFIED,
    )
    session.add(scholarship_source)
    session.commit()

    fetch_calls = {"count": 0}
    extract_calls = {"count": 0}

    def counting_fetch(url: str) -> FetchResult:
        fetch_calls["count"] += 1
        return FetchResult(url=url, success=True, status_code=200, html="<html/>")

    def counting_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        extract_calls["count"] += 1
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    try:
        result = fetch_and_extract_listing(
            session,
            source.id,
            "https://example.com/list",
            fetch=counting_fetch,
            extract_listing=counting_extract_listing,
        )

        assert result.status == "cached"
        assert fetch_calls["count"] == 0
        assert extract_calls["count"] == 0
        assert [ref.scholarship_id for ref in result.cached] == [scholarship.id]
        assert result.cached[0].verification_status == VerificationStatus.UNVERIFIED
    finally:
        session.delete(scholarship_source)
        session.delete(scholarship)
        session.commit()


def test_listing_extraction_ignores_stale_cache_past_ttl(db):
    """A prior successful extraction OLDER than its source's
    update_frequency-derived TTL must not be reused -- the fetch and LLM
    extraction run again for real."""
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active", update_frequency="weekly"))
    created_sources.append(source.id)

    stale_log = source_repo.log_fetch(
        session,
        SourceFetchLogCreate(
            source_id=source.id,
            status=FetchStatus.OK,
            items_found=1,
            retry_count=0,
            started_at=datetime.now(timezone.utc) - timedelta(days=30),  # weekly TTL is 7 days
        ),
    )
    created_logs.append(stale_log.id)

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=True, status_code=200, html="<html>fresh</html>")

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Fresh Scholarship")], accepted_count=1, rejected_count=0
        )

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )

    assert result.status == "ok"
    assert result.candidates[0]["name"] == "Fresh Scholarship"

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs if log.id != stale_log.id]
