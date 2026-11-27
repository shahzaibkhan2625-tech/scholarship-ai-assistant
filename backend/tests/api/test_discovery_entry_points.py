"""003 T462 (FR-PROC-1): the `POST /discovery` rows of the spec's entry-point map, tested from the route.

- Pending-classification queue, enqueue (FR-BUDGET-13): a student's discovery with a refusing gate stores the record
  exactly as today and queues it.
- Provider failures recorded, kept out of health (FR-ERR-1..5): a provider failure during a discovery extraction is a
  `provider_*` row and the source stays active.
The workflow-level tests (`tests/workflows/test_classify_deferral.py`, `tests/sources/test_official_fetch_provider_errors.py`)
prove the logic; these start at the route. Real (rolled-back) DB; fake fetch, fake extractor, fake gate."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agents.discovery.agent import QueryPlanStep, run_discovery
from app.core import llm, llm_gate
from app.core.llm_gate import LlmQuotaExhausted, LlmTemporarilyUnavailable
from app.data.repositories import source_repo
from app.models.classify_pending import ClassifyPending
from app.models.scholarship import Scholarship
from app.models.source import SourceFetchLog, SourceStatus
from app.schemas.source import SourceRegistryCreate
from app.sources.connectors import official_fetch
from app.tools.api_connector import ApiConnectorOutput
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.listing_fetch import ListingFetchOutput
from app.tools.web_fetch import FetchResult

NAME = "Quux Award"  # no rule in `classify` recognises anything in this text


class RefusingGate:
    def acquire(self, request_type, priority):
        raise LlmTemporarilyUnavailable(datetime.now(timezone.utc) + timedelta(hours=1), "daily_allowance_spent")

    def report_provider_error(self, request_type, error):
        return None


class AllowGate:
    def acquire(self, request_type, priority):
        return None

    def report_provider_error(self, request_type, error):
        return None


@pytest.fixture(autouse=True)
def _gate_reset():
    yield
    llm_gate.install_gate(None)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def test_post_discovery_with_a_refusing_gate_stores_the_record_and_queues_it(authed_user, session, monkeypatch):
    llm_gate.install_gate(RefusingGate())
    # a client that exists, so the gate (not "key not configured") is what refuses the call; nothing is sent
    monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=SimpleNamespace()))
    monkeypatch.setattr("app.api.discovery._has_discovery_criteria", lambda profile: True)
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Route Deferral", source_type="api", official_status="official",
            domain=f"route-def-{uuid.uuid4().hex[:10]}.example.com", access_method="api", reliability_level="high",
            status="active", discovery_role=True, country="Germany",
        ),
    )
    session.commit()

    def with_fakes(db, profile, **kwargs):
        def fetch_api(db_arg, source_id, query):
            raw = [{"name": NAME, "deadline": "2027-03-01"}] if query == "Germany" else []
            return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=raw)

        def listing(_db, source_id, _url):
            return ListingFetchOutput(source_id=source_id, status="ok", candidates=[])

        fake_profile = SimpleNamespace(target_countries=["Germany"], target_fields=[], target_degree_level=None, nationality=None)
        return run_discovery(db, fake_profile, fetch_api=fetch_api, listing_fetch=listing)

    monkeypatch.setattr("app.api.discovery.run_discovery", with_fakes)
    response = authed_user["client"].post("/discovery", headers=authed_user["headers"])
    assert response.status_code == 200, response.text

    session.expire_all()
    [record] = session.execute(select(Scholarship).where(Scholarship.name == NAME)).scalars().all()
    assert record.degree_level is None and record.country is None  # stored exactly as today: nothing guessed
    [queued] = session.execute(
        select(ClassifyPending).where(ClassifyPending.scholarship_id == record.id)
    ).scalars().all()
    assert queued.last_reason in {"budget_deferred", "daily_allowance_spent"} and queued.attempts == 0


def test_post_discovery_records_a_provider_failure_and_keeps_the_source_active(authed_user, session, monkeypatch):
    llm_gate.install_gate(AllowGate())
    monkeypatch.setattr("app.api.discovery._has_discovery_criteria", lambda profile: True)
    domain = f"route-pf-{uuid.uuid4().hex[:10]}.example.com"
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Route Provider Failure", source_type="gov", official_status="official", domain=domain,
            access_method="web", discovery_role=True, reliability_level="high", status="active",
            update_frequency="weekly", listing_page_url=f"https://{domain}/scholarships",
        ),
    )
    session.commit()
    step = QueryPlanStep("country", "X", source.id, "gov", "web")
    monkeypatch.setattr("app.agents.discovery.agent.build_query_plan", lambda db, profile: [step])

    def fake_fetch(url):
        return FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>", final_url=url)

    def failing_extract(html, *, source_url):
        raise LlmQuotaExhausted(retry_at=datetime.now(timezone.utc) + timedelta(hours=2))

    real = official_fetch.fetch_and_extract_listing
    monkeypatch.setattr(
        "app.tools.listing_fetch.fetch_and_extract_listing",
        lambda db, source_id, url=None: real(db, source_id, url, fetch=fake_fetch, extract_listing=failing_extract),
    )
    response = authed_user["client"].post("/discovery", headers=authed_user["headers"])
    assert response.status_code == 200, response.text

    session.expire_all()
    rows = session.execute(select(SourceFetchLog).where(SourceFetchLog.source_id == source.id)).scalars().all()
    assert [r.outcome for r in rows if r.outcome] == ["provider_quota"]
    assert source_repo.get_source_any_status(session, source.id).status == SourceStatus.ACTIVE
    health = authed_user["client"].get("/sources/health", headers=authed_user["headers"]).json()
    mine = next(h for h in health if h["source_id"] == str(source.id))
    # not a monitoring run, so it is not in the per-run count; the time of the last provider failure is (all rows)
    assert mine["consecutive_failures"] == 0 and mine["last_provider_failure_at"] is not None
