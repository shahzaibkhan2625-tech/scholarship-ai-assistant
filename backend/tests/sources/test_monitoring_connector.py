"""002 US1 connector additions: `bypass_cache`, `skip_failing_transition`,
`failure_kind` (T177, D3, D5) — plus regression proof that every caller NOT
passing the new keywords (discovery, `listing_fetch_tool`) behaves exactly as
before. Real (rolled-back) DB; all network/LLM mocked."""

import inspect
import uuid
from pathlib import Path

import pytest

from app.data.repositories import source_repo
from app.models.source import FetchStatus, SourceRegistry, SourceStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.sources.connectors.official_fetch import fetch_and_extract_listing, fetch_official
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult

APP_DIR = Path(__file__).resolve().parents[2] / "app"


@pytest.fixture
def session_and_source(db_session_factory):
    session = db_session_factory()
    domain = f"mon-conn-{uuid.uuid4().hex[:10]}.example.com"
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Monitor Connector Test", source_type="gov", official_status="official", domain=domain,
            access_method="web", discovery_role=True, reliability_level="high", status="active",
        ),
    )
    try:
        yield session, source
    finally:
        session.close()


def _ok(url):
    return FetchResult(url=url, success=True, status_code=200, html="<html>page</html>")


def _fail(url):
    return FetchResult(url=url, success=False, status_code=503, error="Server responded with HTTP 503")


def _one(html, *, source_url):
    return ExtractListingOutput(candidates=[ExtractedListingCandidate(name="X")], accepted_count=1, rejected_count=0)


def _zero(html, *, source_url):
    return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)


def _no_sleep(_):
    return None


def _status(session, source_id):
    session.expire_all()
    return session.get(SourceRegistry, source_id).status


# --- D5: skip_failing_transition ----------------------------------------------


def test_fetch_failure_marks_source_failing_by_default_001_unchanged(session_and_source):
    session, source = session_and_source
    result = fetch_official(session, source.id, "https://x.example.com/", fetch=_fail, sleep=_no_sleep, max_attempts=2)
    assert result.success is False
    assert _status(session, source.id) == SourceStatus.FAILING


def test_fetch_failure_with_skip_keeps_source_active_but_still_logs_failure(session_and_source):
    session, source = session_and_source
    result = fetch_official(
        session, source.id, "https://x.example.com/", fetch=_fail, sleep=_no_sleep, max_attempts=2,
        skip_failing_transition=True,
    )
    assert result.success is False and result.failure_kind == "fetch_error"
    assert _status(session, source.id) == SourceStatus.ACTIVE
    [log] = source_repo.get_fetch_logs_for_source(session, source.id)
    assert log.status == FetchStatus.FAIL


def test_empty_extraction_marks_failing_by_default_but_not_with_skip(session_and_source):
    session, source = session_and_source
    skipped = fetch_and_extract_listing(
        session, source.id, None, fetch=_ok, extract_listing=_zero, sleep=_no_sleep,
        bypass_cache=True, skip_failing_transition=True,
    )
    assert (skipped.status, skipped.failure_kind) == ("fail", "empty_extraction")
    assert _status(session, source.id) == SourceStatus.ACTIVE

    default = fetch_and_extract_listing(session, source.id, None, fetch=_ok, extract_listing=_zero, sleep=_no_sleep)
    assert (default.status, default.failure_kind) == ("fail", "empty_extraction")
    assert _status(session, source.id) == SourceStatus.FAILING  # 001 behavior intact


# --- D3: failure_kind -----------------------------------------------------------


def test_failure_kinds_are_set_where_detected(session_and_source):
    session, source = session_and_source
    fetch_failure = fetch_and_extract_listing(
        session, source.id, None, fetch=_fail, extract_listing=_one, sleep=_no_sleep, max_attempts=1,
        skip_failing_transition=True,
    )
    assert (fetch_failure.status, fetch_failure.failure_kind) == ("fail", "fetch_error")

    def off_domain(url):
        return FetchResult(url=url, success=True, status_code=200, html="<html/>", final_url="https://evil.example.org/")

    redirect = fetch_and_extract_listing(
        session, source.id, None, fetch=off_domain, extract_listing=_one, sleep=_no_sleep, skip_failing_transition=True,
    )
    assert (redirect.status, redirect.failure_kind) == ("fail", "redirect_anomaly")

    ok = fetch_and_extract_listing(
        session, source.id, None, fetch=_ok, extract_listing=_one, sleep=_no_sleep, skip_failing_transition=True
    )
    assert (ok.status, ok.failure_kind, ok.accepted_count) == ("ok", None, 1)


def test_failure_kind_does_not_depend_on_error_text(session_and_source):
    session, source = session_and_source

    def odd_error(url):
        return FetchResult(url=url, success=False, error="all your base are belong to us")

    result = fetch_and_extract_listing(
        session, source.id, None, fetch=odd_error, extract_listing=_one, sleep=_no_sleep, max_attempts=1,
        skip_failing_transition=True,
    )
    assert result.failure_kind == "fetch_error"


# --- T177: bypass_cache -----------------------------------------------------------


def test_cached_by_default_and_bypass_cache_forces_a_real_fetch(session_and_source):
    session, source = session_and_source
    url = f"https://{source.domain}/list"
    source_repo.log_fetch(
        session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK, items_found=3, fetched_url=url)
    )
    calls = []

    def counting_fetch(u):
        calls.append(u)
        return _ok(u)

    cached = fetch_and_extract_listing(session, source.id, url, fetch=counting_fetch, extract_listing=_one, sleep=_no_sleep)
    assert cached.status == "cached" and calls == []  # unchanged discovery behavior

    fresh = fetch_and_extract_listing(
        session, source.id, url, fetch=counting_fetch, extract_listing=_one, sleep=_no_sleep, bypass_cache=True
    )
    assert fresh.status == "ok" and calls == [url]


# --- regression: callers that do not opt in ---------------------------------------


@pytest.mark.parametrize("relpath", ["tools/listing_fetch.py", "agents/discovery/agent.py", "tools/official_fetch.py"])
def test_existing_callers_never_pass_the_new_keywords(relpath):
    text = (APP_DIR / relpath).read_text(encoding="utf-8")
    for keyword in ("bypass_cache", "skip_failing_transition"):
        assert keyword not in text, f"{relpath} must not opt in to {keyword}"


def test_new_keywords_default_off_in_signatures():
    for fn in (fetch_official, fetch_and_extract_listing):
        assert inspect.signature(fn).parameters["skip_failing_transition"].default is False
    assert inspect.signature(fetch_and_extract_listing).parameters["bypass_cache"].default is False
