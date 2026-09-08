"""Discovery Agent tests (T089, Blueprint §7.3, §30.3; PRD B3). Runs against
the real DB (skip-if-unreachable, mirrors tests/workflows/test_ingestion.py);
every row created is torn down. All network/LLM calls are mocked — the
agent's own `fetch_official`/`fetch_api` parameters are the mock boundary
(mirrors `tools/search.py`'s injectable `raw_search` pattern), and ingestion's
`classify_service.classify_candidate` is patched exactly as in
tests/workflows/test_ingestion.py.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.discovery.agent import run_discovery
from app.data.repositories import source_repo
from app.models.scholarship import Scholarship, ScholarshipField
from app.models.source import FetchStatus, ScholarshipSource, SourceFetchLog, SourceRegistry
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.api_connector import ApiConnectorOutput
from app.tools.official_fetch import OfficialFetchOutput


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_scholarships: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_scholarships
    finally:
        for scholarship_id in created_scholarships:
            session.query(ScholarshipField).filter(ScholarshipField.scholarship_id == scholarship_id).delete()
            session.query(ScholarshipSource).filter(ScholarshipSource.scholarship_id == scholarship_id).delete()
            row = session.get(Scholarship, scholarship_id)
            if row is not None:
                session.delete(row)
        session.commit()
        for source_id in created_sources:
            session.query(SourceFetchLog).filter(SourceFetchLog.source_id == source_id).delete()
            row = session.get(SourceRegistry, source_id)
            if row is not None:
                session.delete(row)
        session.commit()
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


def test_query_plan_spans_at_least_two_distinct_dimensions(db):
    session, created_sources, _ = db
    _active_api_source(session, created_sources)
    profile = _profile()

    ok_output = ApiConnectorOutput(source_id=uuid.uuid4(), query="x", status="ok", records=[])
    run = run_discovery(session, profile, fetch_api=lambda *a, **k: ok_output)

    dimensions = {step.dimension for step in run.query_plan}
    assert len(dimensions) >= 2
    assert "country" in dimensions
    assert "field" in dimensions


def test_output_always_includes_a_coverage_summary_even_in_the_happy_path(db):
    session, created_sources, _ = db
    _active_api_source(session, created_sources)
    profile = _profile()

    ok_output = ApiConnectorOutput(source_id=uuid.uuid4(), query="x", status="ok", records=[])
    run = run_discovery(session, profile, fetch_api=lambda *a, **k: ok_output)

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

    run = run_discovery(session, profile, fetch_api=failing_fetch_api)

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

    run = run_discovery(session, profile, fetch_api=raising_fetch_api)

    assert any("connector blew up" in err for err in run.fetch_errors)
    assert run.discovery_result.coverage is not None  # still attached despite the exception


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
        run = run_discovery(session, profile, fetch_api=fetch_api_by_query)

    assert run.fetch_errors == []
    assert len(run.discovery_result.results) == 1
    item = run.discovery_result.results[0]
    created_scholarships.append(item.scholarship_id)

    reloaded = session.get(Scholarship, item.scholarship_id)
    assert reloaded is not None
    assert reloaded.name == "Discovery Agent Fixture Scholarship"
    assert item.source_id == source.id
