"""003 T452 (FR-SKIP-8, SC-012, audit P4-OF-3): ingestion's own per-record log rows (`status=ok`,
`items_found=1`, no fetched URL) must not defeat the cross-run extraction cache. Every test starts at
`POST /discovery`; the fetch and the provider are fakes that count their calls."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agents.discovery.agent import QueryPlanStep
from app.core import llm, llm_gate
from app.data.repositories import source_repo
from app.models.source import FetchStatus, SourceFetchLog
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.sources.connectors import official_fetch
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult

LISTING = "https://{domain}/scholarships"


class AllowGate:
    def __init__(self) -> None:
        self.requests = 0

    def acquire(self, request_type, priority):
        self.requests += 1  # one provider request admitted

    def report_provider_error(self, request_type, error):
        return None


@pytest.fixture
def harness(authed_user, db_session_factory, monkeypatch):
    gate = AllowGate()
    llm_gate.install_gate(gate)
    monkeypatch.setattr(
        llm, "get_gemini_client",
        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text="[]"))),
    )
    counts = {"fetch": 0}

    def fake_fetch(url):
        counts["fetch"] += 1
        return FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>", final_url=url)

    def fake_extract(html, *, source_url):
        llm.generate_text("extract")  # the one provider request per real extraction
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Alpha Scholarship")], accepted_count=1, rejected_count=0
        )

    real = official_fetch.fetch_and_extract_listing
    monkeypatch.setattr(
        "app.tools.listing_fetch.fetch_and_extract_listing",
        lambda db, source_id, url=None: real(db, source_id, url, fetch=fake_fetch, extract_listing=fake_extract),
    )
    monkeypatch.setattr("app.api.discovery._has_discovery_criteria", lambda profile: True)
    # Ingestion is not under test: it fails fast, the extraction row and its OK log are still written.
    monkeypatch.setattr("app.agents.discovery.agent.run_ingestion", lambda *a, **k: {})

    session = db_session_factory()
    domain = f"cache-{uuid.uuid4().hex[:10]}.example.com"
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Cache Test", source_type="gov", official_status="official", domain=domain, access_method="web",
            discovery_role=True, reliability_level="high", status="active", update_frequency="weekly",
            listing_page_url=LISTING.format(domain=domain),
        ),
    )
    session.commit()
    step = QueryPlanStep("country", "X", source.id, "gov", "web")
    monkeypatch.setattr("app.agents.discovery.agent.build_query_plan", lambda db, profile: [step])

    def discover():
        response = authed_user["client"].post("/discovery", headers=authed_user["headers"])
        assert response.status_code == 200, response.text
        return response

    def add_ingestion_rows(n):
        for _ in range(n):  # the way run_ingestion writes them: one OK row per record, no fetched URL
            source_repo.log_fetch(
                session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK, items_found=1)
            )

    yield SimpleNamespace(
        session=session, source=source, counts=counts, gate=gate, discover=discover,
        add_ingestion_rows=add_ingestion_rows, domain=domain,
    )
    session.close()
    llm_gate.install_gate(None)


def test_ingestion_rows_do_not_defeat_the_cache(harness):
    harness.discover()
    assert (harness.counts["fetch"], harness.gate.requests) == (1, 1)
    harness.add_ingestion_rows(5)
    harness.discover()
    assert (harness.counts["fetch"], harness.gate.requests) == (1, 1)  # cached: 0 fetches, 0 provider requests


def test_a_changed_target_url_still_refetches(harness):
    harness.discover()
    harness.add_ingestion_rows(3)
    source = source_repo.get_source_by_id(harness.session, harness.source.id)
    source.listing_page_url = f"https://{harness.domain}/other-listing"
    harness.session.commit()
    harness.discover()
    assert harness.counts["fetch"] == 2


def test_an_extraction_older_than_its_interval_still_refetches(harness):
    harness.discover()
    harness.add_ingestion_rows(3)
    row = (
        harness.session.query(SourceFetchLog)
        .filter(SourceFetchLog.source_id == harness.source.id, SourceFetchLog.items_found == 1,
                SourceFetchLog.fetched_url.is_not(None))
        .one()
    )
    row.started_at = datetime.now(timezone.utc) - timedelta(days=8)  # weekly source: interval 7 days
    harness.session.commit()
    harness.discover()
    assert harness.counts["fetch"] == 2


def test_a_source_with_ingestion_rows_only_refetches(harness):
    harness.add_ingestion_rows(4)
    harness.discover()
    assert (harness.counts["fetch"], harness.gate.requests) == (1, 1)
