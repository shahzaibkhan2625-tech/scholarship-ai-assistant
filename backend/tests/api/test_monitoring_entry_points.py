"""003 T462 (FR-PROC-1): capabilities of the monitoring check, tested from their entry points.

The spec's entry-point map names the scheduler tick and `POST /monitoring/runs` (as operator) for the unchanged-page
skip, the per-source cadence, the provider-failure outcomes and (added by this audit) the rules-first extraction. The
workflow-level tests prove the logic; these start at the route and at the tick function and only inject the
network and the provider through the run's own seams. Real (rolled-back) DB; fake fetch, fake extractor, fake clock."""

import functools
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from app.core.llm_gate import LlmQuotaExhausted
from app.data.repositories import monitoring_repo, source_repo
from app.models.source import SourceRegistry
from app.schemas.source import SourceRegistryCreate
from app.scheduling import scheduler as sched_mod
from app.services import classify_backfill
from app.services.classify import ClassifiedScholarshipFields
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor import graph as monitor_graph

CLASSIFIED = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred", funding_status="fully_funded",
    funding_status_confidence="inferred", provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred", field="Computer Science", field_confidence="inferred",
)
HEC_PAGE = (Path(__file__).resolve().parents[1] / "fixtures" / "hec" / "listing.html").read_text(encoding="utf-8")
RULES = {
    "version": 1, "enabled": True, "item_selector": "ul.card-info-container li.card",
    "name": {"attribute": "data-position"}, "detail_url": {"selector": "a.read-more-arrow", "attribute": "href"},
    "provider_from_registry": True, "min_items": 20,
}


class World:
    """One monitored source, a controllable page, a counting extractor and a clock."""

    def __init__(self, factory, rules=None):
        # Short-lived sessions only: the test connection nests every session in a savepoint, so a long-lived older
        # session committing would release the savepoints of the newer sessions the route and the tick open.
        self.factory = factory
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        self.t = self.base
        self.page = "<html><body><ul><li>Alpha Scholarship 2027</li></ul></body></html>"
        self.fetches, self.extractions = 0, 0
        self.error: Exception | None = None
        with factory() as session:
            self.source = self._create(session, rules)
            self.source_id = self.source.id
            self.domain = self.source.domain

    def _create(self, session, rules):
        source = source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name="Entry Point", organization="Entry Org", source_type="gov", official_status="official",
                domain=f"entry-{uuid.uuid4().hex[:10]}.example.com", access_method="web", discovery_role=True,
                reliability_level="high", status="active", update_frequency="weekly",
                extraction_rules={"deterministic": rules} if rules else {},
            ),
        )
        session.commit()
        return source

    def fetch(self, url, headers=None):
        self.fetches += 1
        return FetchResult(url=url, success=True, status_code=200, html=self.page, final_url=url)

    def extract(self, html, *, source_url):
        self.extractions += 1
        if self.error is not None:
            raise self.error
        candidate = ExtractedListingCandidate(name="Alpha Scholarship 2027", degree_level="PhD", deadline="2027-03-01")
        return ExtractListingOutput(candidates=[candidate], accepted_count=1, rejected_count=0)

    def seamed(self, real):
        @functools.wraps(real)
        def run(db, **kwargs):
            return real(db, **kwargs, fetch=self.fetch, extract_listing=self.extract, sleep=lambda _d: None,
                        now=lambda: self.t)

        return run

    def make_due(self):
        """Time passes: the source was last checked long enough ago to be due again."""
        with self.factory() as session:
            session.execute(
                update(SourceRegistry).where(SourceRegistry.id == self.source_id).values(last_checked_at=self.t - timedelta(days=3))
            )
            session.commit()
        self.t += timedelta(hours=1)


@pytest.fixture
def operator(client, make_user):
    return {"client": client, "headers": make_user(is_operator=True)["headers"]}


@pytest.fixture
def shared(db_session_factory, client, monkeypatch):
    """One session for the request AND the route's background body: the test connection nests every session in a
    savepoint, and a request session that closes after the background work would roll that work back."""
    from app.data.repositories.db import get_db
    from app.main import app

    session = db_session_factory()
    monkeypatch.setattr(session, "close", lambda: None)
    previous = app.dependency_overrides.get(get_db)

    def _dep():
        yield session

    app.dependency_overrides[get_db] = _dep
    yield session
    app.dependency_overrides[get_db] = previous


@pytest.fixture
def world(db_session_factory, monkeypatch):
    import app.api.monitoring as api_mod
    import app.data.repositories.db as db_mod

    w = World(db_session_factory)
    monkeypatch.setattr(
        source_repo, "get_monitorable_sources", lambda db, **kw: [db.get(SourceRegistry, w.source_id)]
    )
    monkeypatch.setattr("app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: CLASSIFIED)
    # the tick opens its own session: give it the test connection
    monkeypatch.setattr(db_mod, "SessionLocal", db_session_factory)
    monkeypatch.setattr(api_mod, "run_source_monitor", w.seamed(monitor_graph.run_source_monitor))
    monkeypatch.setattr(monitor_graph, "run_source_monitor", w.seamed(monitor_graph.run_source_monitor))
    monkeypatch.setattr(monitor_graph, "_utcnow", lambda: w.t)  # the tick preflight reads the same clock
    monkeypatch.setattr(classify_backfill, "drain_classify_pending", lambda s, **kw: None)
    with db_session_factory() as session:
        monitoring_repo.fail_leftover_running_runs(session)
    return w


def _post(operator):
    response = operator["client"].post("/monitoring/runs", headers=operator["headers"])
    assert response.status_code == 202
    return operator["client"].get(f"/monitoring/runs/{response.json()['id']}", headers=operator["headers"]).json()


# -- the route as operator ----------------------------------------------------------------------------------------------


def test_route_first_run_extracts_then_nothing_is_due_then_an_unchanged_page_is_skipped(operator, world, shared, monkeypatch):
    import app.api.monitoring as api_mod

    monkeypatch.setattr(api_mod, "SessionLocal", lambda: shared)
    first = _post(operator)
    assert (first["sources_processed"], first["sources_skipped"]) == (1, 0) and world.extractions == 1

    again = _post(operator)  # immediately: the source is not due, so it is not even fetched (FR-CADENCE-5)
    assert (again["sources_processed"], again["sources_skipped"], again["sources_deferred"]) == (0, 0, 0)
    assert world.fetches == 1 and world.extractions == 1

    world.make_due()
    third = _post(operator)  # due again, same page: fetched, skipped as unchanged, no provider request (FR-SKIP-2)
    assert (third["sources_processed"], third["sources_skipped"]) == (1, 1)
    assert world.fetches == 2 and world.extractions == 1
    assert [o["outcome"] for o in third["source_outcomes"] if o["outcome"]] == ["unchanged"]


def test_route_a_provider_failure_is_recorded_and_kept_out_of_health(operator, world, shared, monkeypatch):
    import app.api.monitoring as api_mod

    monkeypatch.setattr(api_mod, "SessionLocal", lambda: shared)
    _post(operator)
    world.make_due()
    world.page = "<html><body><ul><li>Alpha Scholarship 2027</li><li>Beta Grant</li></ul></body></html>"
    world.error = LlmQuotaExhausted(retry_at=world.t + timedelta(hours=2))
    detail = _post(operator)
    assert detail["sources_deferred"] == 1 and detail["sources_failed"] == 0
    assert [o["outcome"] for o in detail["source_outcomes"] if o["outcome"]] == ["provider_quota"]
    health = operator["client"].get("/sources/health", headers=operator["headers"]).json()
    mine = next(h for h in health if h["source_id"] == str(world.source_id))
    assert mine["status"] == "active" and mine["consecutive_failures"] == 0
    assert mine["recent_provider_failures"] == 1 and mine["last_provider_failure_at"] is not None


# -- the tick function ---------------------------------------------------------------------------------------------------


def test_tick_checks_a_due_source_then_skips_it_unchanged_and_an_idle_tick_makes_no_run(world, db_session_factory):
    from sqlalchemy import func, select

    from app.models.monitoring import MonitoringRun

    def runs():
        with db_session_factory() as session:
            return session.execute(select(func.count()).select_from(MonitoringRun)).scalar_one()

    before = runs()
    sched_mod._run_scheduled_monitor()  # never checked: due
    assert world.extractions == 1 and runs() == before + 1
    sched_mod._run_scheduled_monitor()  # just checked: nothing due, no candidate to validate
    assert world.fetches == 1 and runs() == before + 1  # an idle tick creates no run row

    world.make_due()
    sched_mod._run_scheduled_monitor()  # due again, page unchanged: no provider request
    assert world.fetches == 2 and world.extractions == 1 and runs() == before + 2


def test_tick_a_provider_failure_gates_the_next_ticks_until_the_retry_time(world):
    sched_mod._run_scheduled_monitor()
    world.make_due()
    world.page = "<html><body><ul><li>Alpha Scholarship 2027</li><li>Gamma Prize</li></ul></body></html>"
    world.error = LlmQuotaExhausted(retry_at=world.t + timedelta(hours=2))
    sched_mod._run_scheduled_monitor()
    assert world.extractions == 2  # one attempt, refused by the provider
    world.t += timedelta(minutes=30)
    sched_mod._run_scheduled_monitor()
    assert world.extractions == 2 and world.fetches == 2  # not retried on the next tick: `next_attempt_at` gates it
    world.error = None
    world.t += timedelta(hours=2)
    sched_mod._run_scheduled_monitor()
    assert world.extractions == 3  # after the retry time the changed page is extracted


def test_tick_rules_first_extraction_makes_no_provider_request(db_session_factory, monkeypatch):
    import app.data.repositories.db as db_mod

    world = World(db_session_factory, rules=RULES)
    world.page = HEC_PAGE
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda db, **kw: [db.get(SourceRegistry, world.source_id)])
    monkeypatch.setattr("app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: CLASSIFIED)
    monkeypatch.setattr(db_mod, "SessionLocal", db_session_factory)
    monkeypatch.setattr(monitor_graph, "run_source_monitor", world.seamed(monitor_graph.run_source_monitor))
    monkeypatch.setattr(monitor_graph, "_utcnow", lambda: world.t)
    monkeypatch.setattr(classify_backfill, "drain_classify_pending", lambda s, **kw: None)
    with db_session_factory() as session:
        monitoring_repo.fail_leftover_running_runs(session)

    sched_mod._run_scheduled_monitor()
    assert world.extractions == 0  # the rules answered; the provider was never asked (FR-RULES-1)
    from app.data.repositories import scholarship_repo

    with db_session_factory() as session:
        assert len(scholarship_repo.list_for_source(session, world.source_id)) == 37
        rows = source_repo.get_fetch_logs_for_source(session, world.source_id)
        assert [r.outcome for r in rows if r.items_found == 37] == ["rules"]
