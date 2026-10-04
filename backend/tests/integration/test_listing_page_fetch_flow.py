"""T163 (US6): end-to-end listing-page fetch flow, quickstart US6 steps 1-4,
with fixture content only (no live network, no Gemini): `fetch=` and
`extract_listing=` are the mock seams.

1. Seeded source with a distinct listing page -> fetch-log rows show the listing URL.
2. Listing URL cleared -> homepage fetched, `used_homepage_fallback` true.
3. Listing URL changed -> the new URL is fetched on the very next run.
4. Legacy `extraction_rules["list_page_url"]` is honoured when the column is NULL.

Honest scope note (T169): this mocked suite being green says nothing about the
real sites. The weekly `pytest -m smoke` live listing run
(`tests/smoke/test_discovery_listing_extraction_live.py`) does NOT cover DAAD,
Stipendium Hungaricum or KAUST — they stay `discovery_role: false` until a
live check confirms extractable content and the robots review is done.
"""

import uuid

import pytest

from app.data.repositories import source_repo
from app.models.source import FetchStatus
from app.schemas.source import SourceRegistryCreate
from app.sources.connectors.official_fetch import fetch_and_extract_listing
from app.tools import listing_fetch as listing_fetch_module
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _upsert(session, domain, **overrides):
    defaults = dict(
        name="Listing Flow Source", source_type="gov", official_status="official", domain=domain,
        access_method="web", reliability_level="high", status="active", discovery_role=True,
    )
    defaults.update(overrides)
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**defaults))


def _extract(html, *, source_url):
    return ExtractListingOutput(
        candidates=[ExtractedListingCandidate(name="Fixture Scholarship")], accepted_count=1, rejected_count=0
    )


class _Recorder:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        return FetchResult(url=url, success=True, status_code=200, html="<html>fixture listing</html>", final_url=url)


def _rows_for(session, source_id, fetched_url):
    """Rows by `fetched_url`, not recency: the per-test transaction gives every
    row the same `started_at`."""
    return [r for r in source_repo.get_fetch_logs_for_source(session, source_id) if r.fetched_url == fetched_url]


def test_listing_url_clear_and_change_flow(db):
    domain = f"flow-{uuid.uuid4().hex[:12]}.example.com"
    listing = f"https://{domain}/scholarships/"
    source = _upsert(db, domain, listing_page_url=listing)
    fetch = _Recorder()

    # 1. distinct listing page -> log rows show the listing URL
    first = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract, sleep=lambda _d: None)
    assert first.status == "ok"
    assert fetch.urls == [listing]
    rows = _rows_for(db, source.id, listing)
    assert len(rows) == 2  # raw fetch row + extraction row
    assert all(r.used_homepage_fallback is False for r in rows)

    # 2. cleared -> homepage + fallback true (the old listing-URL row must not serve as a cache hit)
    _upsert(db, domain, listing_page_url=None)
    second = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract, sleep=lambda _d: None)
    assert second.status == "ok"
    assert fetch.urls[-1] == f"https://{domain}"
    assert second.used_homepage_fallback is True
    rows = _rows_for(db, source.id, f"https://{domain}")
    assert len(rows) == 2
    assert all(r.used_homepage_fallback is True for r in rows)

    # 3. changed -> new URL on the next run
    changed = f"https://{domain}/new-listing/"
    _upsert(db, domain, listing_page_url=changed)
    third = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract, sleep=lambda _d: None)
    assert third.status == "ok"
    assert fetch.urls[-1] == changed
    rows = _rows_for(db, source.id, changed)
    assert len(rows) == 2
    assert all(r.used_homepage_fallback is False and r.status == FetchStatus.OK for r in rows)


def test_legacy_extraction_rules_key_is_used_when_column_is_null(db):
    domain = f"flow-{uuid.uuid4().hex[:12]}.example.com"
    legacy = f"https://{domain}/legacy-list/"
    source = _upsert(db, domain, extraction_rules={"list_page_url": legacy})
    fetch = _Recorder()

    result = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract, sleep=lambda _d: None)

    assert fetch.urls == [legacy]
    assert result.used_homepage_fallback is False


def test_second_run_with_same_target_is_a_cache_hit(db):
    domain = f"flow-{uuid.uuid4().hex[:12]}.example.com"
    source = _upsert(db, domain, listing_page_url=f"https://{domain}/l/", update_frequency="weekly")
    fetch = _Recorder()

    fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract, sleep=lambda _d: None)
    again = fetch_and_extract_listing(db, source.id, fetch=fetch, extract_listing=_extract, sleep=lambda _d: None)

    assert again.status == "cached"
    assert len(fetch.urls) == 1


def test_listing_fetch_tool_forwards_none_as_third_positional_argument(db, monkeypatch):
    captured: dict = {}

    def fake_connector(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        from app.sources.connectors.official_fetch import ListingExtractionResult

        return ListingExtractionResult(source_id=args[1], status="ok", candidates=[])

    monkeypatch.setattr(listing_fetch_module, "fetch_and_extract_listing", fake_connector)
    sid = uuid.uuid4()

    listing_fetch_module.listing_fetch_tool(db, sid)

    assert captured["args"] == (db, sid, None)
