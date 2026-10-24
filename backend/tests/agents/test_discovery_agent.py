"""Discovery Agent tests (T089, Blueprint §7.3, §30.3; PRD B3). Runs against
the real DB (skip-if-unreachable, mirrors tests/workflows/test_ingestion.py);
every row created is torn down. All network/LLM calls are mocked — the
agent's own `fetch_api`/`listing_fetch` parameters are the mock boundary
(mirrors `tools/search.py`'s injectable `raw_search` pattern), and ingestion's
`classify_service.classify_candidate` is patched exactly as in
tests/workflows/test_ingestion.py.

As of T135, web-source fetching + LLM listing-extraction + grounding + all
`source_fetch_log`/`failing`-status writes live entirely inside
`listing_fetch_tool`'s connector (`tests/sources/test_connectors.py` covers
that machinery directly) — `_ok_listing_fetch` below stands in for "the tool
says this source's listing extraction succeeded with nothing to ingest",
mirroring how `_ok_fetch_official` used to stand in for a blank official
fetch before T135.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.discovery.agent import run_discovery
from app.data.repositories import source_repo
from app.models.scholarship import Scholarship
from app.models.source import FetchStatus, SourceRegistry
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.api_connector import ApiConnectorOutput
from app.tools.listing_fetch import ListingFetchOutput


@pytest.fixture
def db(db_session_factory):
    """`db_session_factory` binds this session to a per-test transaction that
    is rolled back at teardown, so every row created here -- tracked in
    `created_sources`/`created_scholarships` or not -- is discarded without
    needing hand-written, FK-ordered deletes."""
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_scholarships: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_scholarships
    finally:
        session.close()


def _active_api_source(session, created_sources, **overrides) -> SourceRegistry:
    defaults = dict(
        name="Discovery Test API Source",
        source_type="api",
        official_status="official",
        domain=f"discovery-test-{uuid.uuid4().hex}.example.com",
        access_method="api",
        reliability_level="high",
        status="active",
        discovery_role=True,
        country="Germany",
    )
    defaults.update(overrides)
    source = source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**defaults))
    created_sources.append(source.id)
    return source


def _profile(**overrides) -> SimpleNamespace:
    defaults = dict(target_countries=["Germany"], target_fields=["Computer Science"], target_degree_level=None, nationality=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _ok_listing_fetch(_db, source_id, _url) -> ListingFetchOutput:
    """Stub for the `listing_fetch` mock boundary (see module docstring).
    The active-source baseline in the DB includes web/official sources
    outside any single test's own fixtures (e.g. the "field" dimension
    matches every active discovery-role source, not just the one a test
    creates), so without this every test would also make a real HTTP+LLM
    call against those sources' domains."""
    return ListingFetchOutput(source_id=source_id, status="ok", candidates=[])


def test_query_plan_spans_at_least_two_distinct_dimensions(db):
    session, created_sources, _ = db
    _active_api_source(session, created_sources)
    profile = _profile()

    ok_output = ApiConnectorOutput(source_id=uuid.uuid4(), query="x", status="ok", records=[])
    run = run_discovery(session, profile, listing_fetch=_ok_listing_fetch, fetch_api=lambda *a, **k: ok_output)

    dimensions = {step.dimension for step in run.query_plan}
    assert len(dimensions) >= 2
    assert "country" in dimensions
    assert "field" in dimensions


def test_output_always_includes_a_coverage_summary_even_in_the_happy_path(db):
    session, created_sources, _ = db
    _active_api_source(session, created_sources)
    profile = _profile()

    ok_output = ApiConnectorOutput(source_id=uuid.uuid4(), query="x", status="ok", records=[])
    run = run_discovery(session, profile, listing_fetch=_ok_listing_fetch, fetch_api=lambda *a, **k: ok_output)

    assert run.fetch_errors == []
    assert run.discovery_result.coverage is not None
    assert run.discovery_result.coverage.claims_complete_coverage is False


def test_tool_failure_surfaces_as_a_coverage_gap_not_a_silent_empty_result(db):
    session, created_sources, _ = db
    source = _active_api_source(session, created_sources)
    profile = _profile()

    def failing_fetch_api(db_arg, source_id, query):
        # Simulates exactly what the real connector does on failure (T073):
        # a fail row is logged before the failure is reported back up.
        source_repo.log_fetch(
            db_arg,
            SourceFetchLogCreate(source_id=source_id, status=FetchStatus.FAIL, error="upstream unavailable"),
        )
        return ApiConnectorOutput(source_id=source_id, query=query, status="fail", records=[], error="upstream unavailable")

    run = run_discovery(session, profile, listing_fetch=_ok_listing_fetch, fetch_api=failing_fetch_api)

    # Not a silent "no results" — the failure is traced explicitly...
    assert run.fetch_errors != []
    assert any("upstream unavailable" in err for err in run.fetch_errors)
    # ...and shows up as a measured coverage gap, not swallowed.
    coverage = run.discovery_result.coverage
    assert coverage.sources_failed >= 1
    assert any(source.country in gap for gap in coverage.gaps)


def test_tool_call_raising_an_exception_also_surfaces_as_a_traced_failure_not_a_crash(db):
    session, created_sources, _ = db
    _active_api_source(session, created_sources)
    profile = _profile()

    def raising_fetch_api(*_args, **_kwargs):
        raise RuntimeError("connector blew up")

    run = run_discovery(session, profile, listing_fetch=_ok_listing_fetch, fetch_api=raising_fetch_api)

    assert any("connector blew up" in err for err in run.fetch_errors)
    assert run.discovery_result.coverage is not None  # still attached despite the exception


class _FakeProviderError(Exception):
    """Stands in for an unhandled Gemini/provider error raised out of the real
    `listing_fetch_tool` -> `fetch_and_extract_listing` -> `extract_listing_from_page`
    chain (none of which catches it) -- exactly the gap found investigating why
    `tests/smoke/test_discovery_listing_extraction_live.py` returned 0 candidates
    with no trace anywhere: `_run_step`'s broad `except Exception` caught it
    silently, and `app.api.discovery.discover()` never returns or logs
    `fetch_errors`, so nothing about the failure was ever observable."""


def test_web_listing_step_exception_is_logged_with_source_and_type_not_silently_swallowed(db, caplog):
    import logging

    session, created_sources, _ = db
    domain = f"discovery-test-{uuid.uuid4().hex}.example.com"
    source = _active_api_source(
        session,
        created_sources,
        name="Discovery Test Web Source (provider error)",
        source_type="gov",
        access_method="web",
        domain=domain,
    )
    profile = _profile()

    def ok_api(db_arg, source_id, query):
        return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=[])

    secret_like = "sk-fake-not-a-real-secret-zzz999"  # allow-secret: planted fake, asserted absent from logs
    page_like = "<html>" + "scholarship listing content " * 50 + "</html>"

    def raising_listing_fetch(_db, _source_id, _url):
        raise _FakeProviderError(f"provider call failed ({secret_like}) while parsing: {page_like}")

    with caplog.at_level(logging.ERROR):
        run = run_discovery(session, profile, listing_fetch=raising_listing_fetch, fetch_api=ok_api)

    # (a) never a silent empty result and never a crash: the run completes,
    # this source contributes nothing, and a coverage summary is still
    # attached -- exactly the "still 200, 0 results for that source" contract
    # `app.api.discovery.discover()` forwards `run.discovery_result` under
    # unchanged (no new raise reaches it).
    assert all(item.source_id != source.id for item in run.discovery_result.results)
    assert run.discovery_result.coverage is not None
    assert any(str(source.id) in err for err in run.fetch_errors)

    # (b) the swallow is no longer silent: something was actually logged,
    # naming the source and the exception type.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "expected the swallowed listing-fetch exception to be logged"
    logged_text = "\n".join(r.getMessage() for r in error_records)
    assert domain in logged_text
    assert "_FakeProviderError" in logged_text

    # Never log secrets or the raw page content the exception happened to carry.
    assert secret_like not in logged_text
    assert page_like not in logged_text


_FAKE_CLASSIFICATION = ClassifiedScholarshipFields(
    degree_level="MS",
    degree_level_confidence="inferred",
    funding_status="fully_funded",
    funding_status_confidence="inferred",
    provider_type="api",
    provider_type_confidence="inferred",
    country="Germany",
    country_confidence="inferred",
    field="Computer Science",
    field_confidence="inferred",
)


def test_found_candidate_is_handed_to_the_existing_ingestion_workflow(db):
    """Proves the agent delegates to T087 rather than reimplementing
    extraction/normalization itself: a candidate record surfaces as a real,
    persisted Scholarship row with a scholarship_id in the final result.

    The fake `fetch_api` returns a candidate only for the "country" plan
    step's query and nothing for the "field" step's — mirroring how two
    distinct §30.3 query-plan dimensions against the same source legitimately
    return distinct result sets in a real API. (Note: `ingestion`'s STORE
    node computes a dedup decision but doesn't yet consult it to skip
    re-storage — T087, out of scope here — so if two plan steps against the
    same source DID resolve to identical raw content, they would currently
    be stored as two separate scholarship rows; flagged as a follow-up gap,
    not something this fixture should paper over by coincidence.)"""
    session, created_sources, created_scholarships = db
    source = _active_api_source(session, created_sources)
    profile = _profile()

    candidate_record = {"name": "Discovery Agent Fixture Scholarship", "country": "Germany", "deadline": "2027-01-01"}

    def fetch_api_by_query(db_arg, source_id, query):
        records = [candidate_record] if query == "Germany" else []
        return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=records)

    with patch("app.workflows.ingestion.graph.classify_service.classify_candidate", return_value=_FAKE_CLASSIFICATION):
        run = run_discovery(session, profile, listing_fetch=_ok_listing_fetch, fetch_api=fetch_api_by_query)

    assert run.fetch_errors == []
    assert len(run.discovery_result.results) == 1
    item = run.discovery_result.results[0]
    created_scholarships.append(item.scholarship_id)

    reloaded = session.get(Scholarship, item.scholarship_id)
    assert reloaded is not None
    assert reloaded.name == "Discovery Agent Fixture Scholarship"
    assert item.source_id == source.id


# --- T161 (US6 Scenario 2): T140 regression --------------------------------------


def test_listing_step_does_not_build_a_homepage_url_when_a_listing_url_is_configured(db):
    """T140: `_run_web_listing_step` used to pass `f"https://{source.domain}"`
    to the listing-fetch tool, ignoring the configured listing page. It must
    now delegate URL choice to the connector by passing `None`."""
    session, created_sources, _ = db
    domain = f"discovery-test-{uuid.uuid4().hex}.example.com"
    source = _active_api_source(
        session,
        created_sources,
        name="Discovery Test Web Source",
        source_type="gov",
        access_method="web",
        domain=domain,
        listing_page_url=f"https://{domain}/scholarships/",
    )
    calls: list[tuple] = []

    def recording_listing_fetch(_db, source_id, url=None) -> ListingFetchOutput:
        calls.append((source_id, url))
        return ListingFetchOutput(source_id=source_id, status="ok", candidates=[])

    def ok_api(db_arg, source_id, query):
        return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=[])

    run_discovery(session, _profile(), listing_fetch=recording_listing_fetch, fetch_api=ok_api)

    mine = [call for call in calls if call[0] == source.id]
    assert mine, "the web source's listing step never ran"
    for _source_id, url in mine:
        assert url is None


def test_offline_discovery_flow_two_new_web_sources_with_correctly_mocked_extraction(db):
    """Regression test (2026-10-24): reproduces the LIVE smoke test's exact shape
    (two brand-new web sources, one international_org + one gov, no
    listing_page_url -> homepage fallback) but with fully offline mocks: a
    fake `httpx.get` (no network) and a fake `extract_listing_from_page` (no
    LLM) returning grounded candidates directly. Everything else -- the real
    `listing_fetch_tool` -> `fetch_and_extract_listing` -> `resolve_fetch_target`
    chain, caching, redirect checks -- runs unmocked, exactly as US6 left it.

    This proves the discovery/connector code itself is not regressed: when
    `extract_listing` actually returns grounded candidates, `run_discovery`
    returns them. It isolates discovery's own logic from two separate,
    already-identified problems: (1) `tests/smoke/test_discovery_listing_extraction_live.py`'s
    own `_fake_httpx_get` omits `.url`, which crashes `fetch_url`
    (`app/tools/web_fetch.py`, `final_url=str(response.url)`, added in US6's
    `0205d23` after the smoke test was last touched) -- fixed alongside this
    test; and (2) whatever the real Gemini call/grounding actually does in
    production, which this test deliberately does not exercise."""
    from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput

    session, created_sources, created_scholarships = db

    intl_domain = f"discovery-test-intl-{uuid.uuid4().hex}.example.com"
    gov_domain = f"discovery-test-gov-{uuid.uuid4().hex}.example.com"
    intl_country = f"Offline-Intl-{uuid.uuid4().hex[:8]}"
    gov_country = f"Offline-Gov-{uuid.uuid4().hex[:8]}"

    intl_source = _active_api_source(
        session, created_sources,
        name="Offline Test Intl Source", source_type="international_org",
        access_method="web", domain=intl_domain, country=intl_country,
    )
    gov_source = _active_api_source(
        session, created_sources,
        name="Offline Test Gov Source", source_type="gov",
        access_method="web", domain=gov_domain, country=gov_country,
    )
    profile = _profile(target_countries=[intl_country, gov_country], target_fields=[])

    def ok_api(db_arg, source_id, query):
        return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=[])

    def fake_httpx_get(url: str, **_kwargs) -> SimpleNamespace:
        # Correctly set `.url` -- the fix for the bug this test exists to isolate.
        return SimpleNamespace(status_code=200, text="<html>offline fixture</html>", url=url)

    def fake_extract_listing(_html: str, *, source_url: str) -> ExtractListingOutput:
        if intl_domain in source_url:
            candidate = ExtractedListingCandidate(name="Offline Intl Fixture Scholarship")
        else:
            candidate = ExtractedListingCandidate(name="Offline Gov Fixture Scholarship")
        return ExtractListingOutput(candidates=[candidate], accepted_count=1, rejected_count=0)

    # `fetch_and_extract_listing`'s `extract_listing` parameter defaults to the
    # real `extract_listing_from_page` *at def time*: patching that name on the
    # `official_fetch` module afterward does not change the already-bound
    # default, so `listing_fetch_tool`'s plain (no-kwarg) call would silently
    # keep using the real one. Patch the name `listing_fetch_tool` itself looks
    # up (a normal per-call lookup, not a bound default) with a thin wrapper
    # that forces the real function's `extract_listing` kwarg to our fake,
    # while still running every other real step (resolve_fetch_target, the
    # two-layer cache, redirect checks, logging, mark_source_failing).
    import app.sources.connectors.official_fetch as official_fetch_module

    real_fetch_and_extract_listing = official_fetch_module.fetch_and_extract_listing

    def wrapped_fetch_and_extract_listing(db_arg, source_id, url=None, **kwargs):
        kwargs.setdefault("extract_listing", fake_extract_listing)
        return real_fetch_and_extract_listing(db_arg, source_id, url, **kwargs)

    with patch("app.tools.web_fetch.httpx.get", side_effect=fake_httpx_get), \
         patch("app.tools.listing_fetch.fetch_and_extract_listing", side_effect=wrapped_fetch_and_extract_listing), \
         patch("app.workflows.ingestion.graph.classify_service.classify_candidate", return_value=_FAKE_CLASSIFICATION):
        run = run_discovery(session, profile, fetch_api=ok_api)

    our_results = [
        item for item in run.discovery_result.results
        if item.source_id in {intl_source.id, gov_source.id}
    ]
    for item in our_results:
        created_scholarships.append(item.scholarship_id)

    assert len(our_results) >= 2, (
        f"offline mocked discovery flow returned fewer than 2 candidates for 2 fresh web "
        f"sources -- this IS a code regression. got: {our_results}, fetch_errors={run.fetch_errors}"
    )


def test_agent_module_does_not_import_the_connector_or_resolver_or_build_a_homepage_url():
    import ast
    import inspect

    from app.agents.discovery import agent as discovery_agent_module

    text = inspect.getsource(discovery_agent_module)
    imported: list[str] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
    assert not any("connectors" in name or name == "resolve_fetch_target" for name in imported)
    listing_step = text.split("def _run_web_listing_step")[1].split("def _run_step")[0]
    assert 'f"https://{source.domain}"' not in listing_step
