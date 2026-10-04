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
from app.models.source import (
    CandidateSource,
    FetchStatus,
    ScholarshipSource,
    SourceFetchLog,
    SourceRegistry,
    VerificationStatus,
)
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


@pytest.fixture
def db_with_candidates(db_session_factory):
    """Like `db` above, plus `candidate_sources` teardown — for the
    candidate-detection wiring tests only (US4 Acceptance Scenario 3,
    ADR-0005), so the existing `db` fixture/tests above stay untouched."""
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_logs: list[uuid.UUID] = []
    created_candidates: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_logs, created_candidates
    finally:
        for candidate_id in created_candidates:
            row = session.get(CandidateSource, candidate_id)
            if row is not None:
                session.delete(row)
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


# --- candidate-source detection wiring (US4 Acceptance Scenario 3, ADR-0005) --


def _find_candidates_for_domain(session, domain: str) -> list[CandidateSource]:
    return [
        c
        for c in source_repo.list_candidate_sources(session, status=None)
        if source_repo.domain_from_url(c.url) == domain
    ]


def test_successful_fetch_with_unrecognized_scholarship_link_records_a_pending_candidate(db_with_candidates):
    session, created_sources, created_logs, created_candidates = db_with_candidates
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)
    new_domain = f"new-scholarship-fund-{uuid.uuid4().hex}.example.org"

    def fake_fetch(url: str) -> FetchResult:
        html = f'<html><body><a href="https://{new_domain}/apply">New Scholarship Fund</a></body></html>'
        return FetchResult(url=url, success=True, status_code=200, html=html)

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]

    matching = _find_candidates_for_domain(session, new_domain)
    created_candidates += [c.id for c in matching]

    assert len(matching) == 1
    assert matching[0].status == "pending"
    assert matching[0].signals["matched_keyword"] == "scholarship"


def test_candidate_detection_runs_even_when_listing_extraction_yields_zero_accepted(db_with_candidates):
    """Detection is independent of whether the listing-extraction step found
    any scholarships on the page -- it runs over the raw HTML regardless."""
    session, created_sources, created_logs, created_candidates = db_with_candidates
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)
    new_domain = f"zero-accepted-fund-{uuid.uuid4().hex}.example.org"

    def fake_fetch(url: str) -> FetchResult:
        html = f'<html><body><a href="https://{new_domain}/apply">New Fellowship</a></body></html>'
        return FetchResult(url=url, success=True, status_code=200, html=html)

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )
    assert result.status == "fail"  # zero-accepted-candidates path (T135/A5+A8), unaffected by detection

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]

    matching = _find_candidates_for_domain(session, new_domain)
    created_candidates += [c.id for c in matching]
    assert len(matching) == 1


def test_page_with_no_qualifying_links_records_no_candidate(db_with_candidates):
    session, created_sources, created_logs, _ = db_with_candidates
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=True, status_code=200, html="<html><body>no links here</body></html>")

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Some Scholarship")], accepted_count=1, rejected_count=0
        )

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )
    assert result.status == "ok"

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]


def test_detection_failure_never_affects_the_listing_extraction_result(db_with_candidates, monkeypatch):
    """ADR-0005 Decision 4 / A7: a detection-layer exception must never
    surface as a fetch failure or otherwise change what the listing step
    returns -- it is swallowed entirely."""
    session, created_sources, created_logs, _ = db_with_candidates
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    def broken_find_candidate_links(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.sources.connectors.official_fetch.find_candidate_links", broken_find_candidate_links
    )

    def fake_fetch(url: str) -> FetchResult:
        return FetchResult(url=url, success=True, status_code=200, html="<html><body>irrelevant</body></html>")

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Some Scholarship")], accepted_count=1, rejected_count=0
        )

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )

    assert result.status == "ok"
    assert result.candidates == [{"name": "Some Scholarship", "funding_status": "unknown"}]

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]


def test_already_active_domain_link_records_no_new_candidate(db_with_candidates):
    session, created_sources, created_logs, _ = db_with_candidates
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)
    other_active = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(other_active.id)

    def fake_fetch(url: str) -> FetchResult:
        html = f'<html><body><a href="https://{other_active.domain}/scholarships">Scholarship List</a></body></html>'
        return FetchResult(url=url, success=True, status_code=200, html=html)

    def fake_extract_listing(html: str, *, source_url: str) -> ExtractListingOutput:
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    fetch_and_extract_listing(
        session, source.id, "https://example.com/list", fetch=fake_fetch, extract_listing=fake_extract_listing
    )

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    created_logs += [log.id for log in logs]

    matching = _find_candidates_for_domain(session, other_active.domain)
    assert matching == []


# --- T160 (US6): fetched_url / used_homepage_fallback / monitoring_run_id ------


def _ok_fetch(url: str) -> FetchResult:
    return FetchResult(url=url, success=True, status_code=200, html="<html>page</html>")


def _fail_fetch(url: str) -> FetchResult:
    return FetchResult(url=url, success=False, status_code=503, error="Server responded with HTTP 503")


def _one_candidate_extract(html: str, *, source_url: str) -> ExtractListingOutput:
    return ExtractListingOutput(
        candidates=[ExtractedListingCandidate(name="Listed Scholarship")], accepted_count=1, rejected_count=0
    )


def _zero_extract(html: str, *, source_url: str) -> ExtractListingOutput:
    return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)


def _track(created_logs, session, source_id):
    logs = source_repo.get_fetch_logs_for_source(session, source_id)
    created_logs += [log.id for log in logs]
    return logs


def _new_monitoring_run(session):
    from app.models.monitoring import MonitoringRun, MonitoringTrigger

    run = MonitoringRun(trigger=MonitoringTrigger.MANUAL)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _drop_logs_and_run(session, source_ids, run):
    for sid in source_ids:
        for log in source_repo.get_fetch_logs_for_source(session, sid):
            session.delete(log)
    session.commit()
    session.delete(run)
    session.commit()


def test_fetch_official_success_row_carries_fetched_url_and_fallback_and_run_id(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)
    run = _new_monitoring_run(session)
    try:
        result = fetch_official(
            session, source.id, "https://example.com/page", fetch=_ok_fetch, sleep=_no_sleep,
            used_homepage_fallback=True, monitoring_run_id=run.id,
        )
        assert result.success is True
        [log] = source_repo.get_fetch_logs_for_source(session, source.id)
        assert log.status == FetchStatus.OK
        assert log.fetched_url == "https://example.com/page"
        assert log.used_homepage_fallback is True
        assert log.monitoring_run_id == run.id
    finally:
        _drop_logs_and_run(session, [source.id], run)


def test_fetch_official_failure_row_carries_fetched_url_and_fallback_and_run_id(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)
    run = _new_monitoring_run(session)
    try:
        result = fetch_official(
            session, source.id, "https://example.com/page", fetch=_fail_fetch, sleep=_no_sleep, max_attempts=2,
            used_homepage_fallback=False, monitoring_run_id=run.id,
        )
        assert result.success is False
        [log] = source_repo.get_fetch_logs_for_source(session, source.id)
        assert log.status == FetchStatus.FAIL
        assert log.fetched_url == "https://example.com/page"
        assert log.used_homepage_fallback is False
        assert log.monitoring_run_id == run.id
    finally:
        _drop_logs_and_run(session, [source.id], run)


def test_fetch_official_without_new_kwargs_still_derives_fetched_url(db):
    session, created_sources, created_logs = db
    source = source_repo.upsert_source_by_domain(session, _source_data(status="active"))
    created_sources.append(source.id)

    fetch_official(session, source.id, "https://example.com/page", fetch=_ok_fetch, sleep=_no_sleep)
    [log] = _track(created_logs, session, source.id)
    assert log.fetched_url == "https://example.com/page"
    assert log.used_homepage_fallback is False
    assert log.monitoring_run_id is None


def test_explicit_url_listing_extraction_is_001_identical_and_logs_fetched_url(db):
    """Explicit positional URL => (url, False): never resolved, never fallback,
    even when the source has its own listing_page_url."""
    session, created_sources, created_logs = db
    domain = f"connector-test-{uuid.uuid4().hex}.example.com"
    source = source_repo.upsert_source_by_domain(
        session, _source_data(status="active", domain=domain, listing_page_url=f"https://{domain}/configured/")
    )
    created_sources.append(source.id)
    seen: list[str] = []

    def fetch(url: str) -> FetchResult:
        seen.append(url)
        return _ok_fetch(url)

    result = fetch_and_extract_listing(
        session, source.id, "https://example.com/explicit", fetch=fetch, extract_listing=_one_candidate_extract
    )

    assert seen == ["https://example.com/explicit"]
    assert result.status == "ok"
    assert result.fetched_url == "https://example.com/explicit"
    assert result.used_homepage_fallback is False
    logs = _track(created_logs, session, source.id)
    assert len(logs) == 2  # raw fetch row + extraction row
    for log in logs:
        assert log.fetched_url == "https://example.com/explicit"
        assert log.used_homepage_fallback is False


def test_all_four_listing_log_sites_carry_the_three_fields(db):
    """Sites: fetch_official OK, fetch_official FAIL, extraction FAIL (zero
    candidates), extraction OK. The resolved path with no listing URL uses the
    homepage => fallback true on every row."""
    session, created_sources, created_logs = db
    run = _new_monitoring_run(session)
    ids = []
    for _ in range(3):
        src = source_repo.upsert_source_by_domain(
            session, _source_data(status="active", domain=f"connector-test-{uuid.uuid4().hex}.example.com")
        )
        created_sources.append(src.id)
        ids.append(src.id)
    try:
        ok = fetch_and_extract_listing(
            session, ids[0], fetch=_ok_fetch, extract_listing=_one_candidate_extract,
            sleep=_no_sleep, monitoring_run_id=run.id,
        )
        assert ok.status == "ok" and ok.used_homepage_fallback is True
        assert ok.fetched_url.startswith("https://connector-test-")
        zero = fetch_and_extract_listing(
            session, ids[1], fetch=_ok_fetch, extract_listing=_zero_extract,
            sleep=_no_sleep, monitoring_run_id=run.id,
        )
        assert zero.status == "fail" and zero.used_homepage_fallback is True
        failed = fetch_and_extract_listing(
            session, ids[2], fetch=_fail_fetch, extract_listing=_zero_extract,
            sleep=_no_sleep, max_attempts=1, monitoring_run_id=run.id,
        )
        assert failed.status == "fail" and failed.used_homepage_fallback is True

        all_logs = []
        for sid in ids:
            all_logs += source_repo.get_fetch_logs_for_source(session, sid)
        assert len(all_logs) == 5  # ok: fetch+extract, zero: fetch+extract-fail, failed: fetch-fail
        for log in all_logs:
            assert log.fetched_url is not None and log.fetched_url.startswith("https://connector-test-")
            assert log.used_homepage_fallback is True
            assert log.monitoring_run_id == run.id
    finally:
        _drop_logs_and_run(session, ids, run)


def test_resolved_path_cache_ignores_row_with_null_fetched_url(db):
    """D: on the resolved path (url None) a pre-002 row (fetched_url NULL)
    never counts as a cache hit."""
    session, created_sources, created_logs = db
    domain = f"connector-test-{uuid.uuid4().hex}.example.com"
    source = source_repo.upsert_source_by_domain(
        session,
        _source_data(status="active", domain=domain, update_frequency="weekly", listing_page_url=f"https://{domain}/l/"),
    )
    created_sources.append(source.id)
    prior = source_repo.log_fetch(
        session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK, items_found=1, retry_count=0)
    )
    created_logs.append(prior.id)

    result = fetch_and_extract_listing(
        session, source.id, fetch=_ok_fetch, extract_listing=_one_candidate_extract, sleep=_no_sleep
    )
    assert result.status == "ok"
    _track(created_logs, session, source.id)


def test_resolved_path_cache_hits_only_when_fetched_url_equals_target(db):
    session, created_sources, created_logs = db
    domain = f"connector-test-{uuid.uuid4().hex}.example.com"
    target = f"https://{domain}/l/"
    source = source_repo.upsert_source_by_domain(
        session, _source_data(status="active", domain=domain, update_frequency="weekly", listing_page_url=target)
    )
    created_sources.append(source.id)
    prior = source_repo.log_fetch(
        session,
        SourceFetchLogCreate(
            source_id=source.id, status=FetchStatus.OK, items_found=1, retry_count=0, fetched_url=target
        ),
    )
    created_logs.append(prior.id)

    def must_not_fetch(url: str) -> FetchResult:
        raise AssertionError("cache hit must not fetch")

    hit = fetch_and_extract_listing(session, source.id, fetch=must_not_fetch, extract_listing=_zero_extract)
    assert hit.status == "cached"

    # The operator changes the listing URL: the old row no longer matches.
    source_repo.upsert_source_by_domain(
        session,
        _source_data(
            status="active", domain=domain, update_frequency="weekly", listing_page_url=f"https://{domain}/new/"
        ),
    )
    miss = fetch_and_extract_listing(
        session, source.id, fetch=_ok_fetch, extract_listing=_one_candidate_extract, sleep=_no_sleep
    )
    assert miss.status == "ok"
    assert miss.fetched_url == f"https://{domain}/new/"
    _track(created_logs, session, source.id)
