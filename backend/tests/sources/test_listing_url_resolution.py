"""T159 (US6): `resolve_fetch_target(source)` precedence + the 404 / redirect
anomaly handling on the resolved path. No live network: `fetch=` is the mock
seam. Pure-function tests need no DB; the anomaly tests run against the real
DB inside the per-test rolled-back transaction (`db_session_factory`).

Precedence: `listing_page_url` -> legacy `extraction_rules["list_page_url"]`
-> `https://{domain}` (the only branch with `used_homepage_fallback=True`).
Every candidate must be http(s) with hostname == source.domain; an invalid
candidate is skipped (logged) and the next tier tried; it never raises.
"""

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.data.repositories import source_repo
from app.models.source import FetchStatus
from app.schemas.source import SourceRegistryCreate
from app.sources.connectors.official_fetch import FetchTarget, fetch_and_extract_listing, resolve_fetch_target
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult

# Same list as tests/data/test_source_repo_002.py::test_registry_create_rejects_bad_listing_url.
_BAD_URLS = [
    "ftp://www2.daad.de/x",
    "javascript:alert(1)",
    "//www2.daad.de/x",
    "www2.daad.de/x",
    "https://evil.example.org/x",
    "https://www2.daad.de.evil.example.org/x",
    "https://sub.www2.daad.de/x",
]
_DOMAIN = "www2.daad.de"


def _src(listing_page_url=None, extraction_rules=None, domain=_DOMAIN):
    return SimpleNamespace(listing_page_url=listing_page_url, extraction_rules=extraction_rules, domain=domain)


# --- pure precedence ----------------------------------------------------------


def test_column_wins_over_legacy_key_and_homepage():
    target = resolve_fetch_target(
        _src("https://www2.daad.de/col/", {"list_page_url": "https://www2.daad.de/legacy/"})
    )
    assert target == FetchTarget("https://www2.daad.de/col/", False)
    assert target.url == "https://www2.daad.de/col/"
    assert target.used_homepage_fallback is False


def test_legacy_extraction_rules_key_used_when_column_null():
    target = resolve_fetch_target(_src(None, {"list_page_url": "https://www2.daad.de/legacy/"}))
    assert target == FetchTarget("https://www2.daad.de/legacy/", False)


def test_homepage_fallback_only_on_last_branch():
    target = resolve_fetch_target(_src(None, {}))
    assert target == FetchTarget("https://www2.daad.de", True)


@pytest.mark.parametrize("rules", [None, {}, {"list_page_url": None}, {"other": 1}])
def test_missing_or_none_extraction_rules_tolerated(rules):
    target = resolve_fetch_target(_src(None, rules))
    assert target.used_homepage_fallback is True
    assert target.url == "https://www2.daad.de"


def test_changing_the_column_changes_the_result_on_the_next_call():
    source = _src("https://www2.daad.de/one/", {})
    assert resolve_fetch_target(source).url == "https://www2.daad.de/one/"
    source.listing_page_url = "https://www2.daad.de/two/"
    assert resolve_fetch_target(source).url == "https://www2.daad.de/two/"
    source.listing_page_url = None
    assert resolve_fetch_target(source) == FetchTarget("https://www2.daad.de", True)


def test_host_compare_is_case_insensitive():
    target = resolve_fetch_target(_src("HTTPS://WWW2.DAAD.DE/x", {}))
    assert target == FetchTarget("HTTPS://WWW2.DAAD.DE/x", False)


@pytest.mark.parametrize("bad", _BAD_URLS)
def test_invalid_column_candidate_is_skipped_to_legacy_tier(bad):
    target = resolve_fetch_target(_src(bad, {"list_page_url": "https://www2.daad.de/legacy/"}))
    assert target == FetchTarget("https://www2.daad.de/legacy/", False)


@pytest.mark.parametrize("bad", _BAD_URLS)
def test_invalid_candidates_everywhere_fall_back_to_homepage_never_raise(bad):
    target = resolve_fetch_target(_src(bad, {"list_page_url": bad}))
    assert target == FetchTarget("https://www2.daad.de", True)


def test_invalid_candidate_logs_a_warning(caplog):
    with caplog.at_level("WARNING"):
        resolve_fetch_target(_src("https://evil.example.org/x", {}))
    assert any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.parametrize("bad", _BAD_URLS)
def test_resolver_and_schema_validator_agree_on_bad_urls(bad):
    """Parity: whatever `SourceRegistryCreate` rejects, the resolver skips."""
    with pytest.raises(ValidationError):
        SourceRegistryCreate(
            name="x", source_type="gov", official_status="official", domain=_DOMAIN,
            access_method="web", reliability_level="high", listing_page_url=bad,
        )
    assert resolve_fetch_target(_src(bad, {})).used_homepage_fallback is True


# --- 404 / redirect anomalies (resolved path only) ----------------------------


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _make_source(session, **overrides):
    domain = f"resolve-test-{uuid.uuid4().hex[:12]}.example.com"
    defaults = dict(
        name="Resolution Test Source", source_type="gov", official_status="official",
        domain=domain, access_method="web", reliability_level="high", status="active",
        listing_page_url=f"https://{domain}/scholarships/",
    )
    defaults.update(overrides)
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**defaults))


def _no_sleep(_delay):
    return None


def _extract_ok(html, *, source_url):
    return ExtractListingOutput(
        candidates=[ExtractedListingCandidate(name="X")], accepted_count=1, rejected_count=0
    )


def _extract_must_not_run(html, *, source_url):
    raise AssertionError("extraction must not run after a redirect anomaly")


def _fail_log(session, source_id):
    """The per-test transaction gives every row the same `started_at`, so pick
    the FAIL row by status rather than by recency."""
    [log] = [row for row in source_repo.get_fetch_logs_for_source(session, source_id) if row.status == FetchStatus.FAIL]
    return log


def test_404_on_listing_url_is_a_fail_row_with_no_homepage_retry(db):
    source = _make_source(db)
    target = source.listing_page_url
    seen: list[str] = []

    def fetch(url):
        seen.append(url)
        return FetchResult(url=url, success=False, status_code=404, error="Server responded with HTTP 404")

    result = fetch_and_extract_listing(
        db, source.id, fetch=fetch, extract_listing=_extract_must_not_run, sleep=_no_sleep, max_attempts=1
    )

    assert result.status == "fail"
    assert set(seen) == {target}  # never retried against the homepage
    log = _fail_log(db, source.id)
    assert log.status == FetchStatus.FAIL
    assert log.http_status == 404
    assert log.fetched_url == target
    assert log.used_homepage_fallback is False


def test_redirect_to_homepage_is_a_fail_not_an_intended_homepage(db):
    source = _make_source(db)
    target = source.listing_page_url

    def fetch(url):
        return FetchResult(url=url, success=True, status_code=200, html="<html/>", final_url=f"https://{source.domain}/")

    result = fetch_and_extract_listing(
        db, source.id, fetch=fetch, extract_listing=_extract_must_not_run, sleep=_no_sleep
    )

    assert result.status == "fail"
    assert result.error == f"redirected to homepage: {target}"
    log = _fail_log(db, source.id)
    assert log.status == FetchStatus.FAIL
    assert log.error == f"redirected to homepage: {target}"
    assert log.fetched_url == target
    assert log.used_homepage_fallback is False


def test_redirect_to_homepage_without_trailing_slash_is_also_caught(db):
    source = _make_source(db)

    def fetch(url):
        return FetchResult(url=url, success=True, status_code=200, html="<html/>", final_url=f"https://{source.domain}")

    result = fetch_and_extract_listing(
        db, source.id, fetch=fetch, extract_listing=_extract_must_not_run, sleep=_no_sleep
    )
    assert result.status == "fail"
    assert result.error.startswith("redirected to homepage: ")


def test_off_domain_redirect_is_a_fail_and_skips_extraction(db):
    source = _make_source(db)

    def fetch(url):
        return FetchResult(
            url=url, success=True, status_code=200, html="<html/>", final_url="https://evil.example.org/landing"
        )

    result = fetch_and_extract_listing(
        db, source.id, fetch=fetch, extract_listing=_extract_must_not_run, sleep=_no_sleep
    )

    assert result.status == "fail"
    assert result.error == "off-domain redirect: https://evil.example.org/landing"
    log = _fail_log(db, source.id)
    assert log.status == FetchStatus.FAIL
    assert log.error == "off-domain redirect: https://evil.example.org/landing"


def test_www_difference_is_not_an_off_domain_redirect(db):
    source = _make_source(db)

    def fetch(url):
        return FetchResult(
            url=url, success=True, status_code=200, html="<html/>",
            final_url=f"https://www.{source.domain}/scholarships/",
        )

    result = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract_ok, sleep=_no_sleep)
    assert result.status == "ok"


def test_homepage_target_redirecting_to_homepage_is_not_flagged(db):
    """The homepage-redirect rule only applies when the resolved target was NOT the homepage."""
    source = _make_source(db, listing_page_url=None)

    def fetch(url):
        return FetchResult(url=url, success=True, status_code=200, html="<html/>", final_url=f"https://{source.domain}/")

    result = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract_ok, sleep=_no_sleep)
    assert result.status == "ok"
    assert result.used_homepage_fallback is True
