"""003 T279 (FR-SKIP-2/6, R-07): the monitor's `unchanged` branch. A skipped check is a successful, cheap
check: health and the check timestamps move, nothing else does (no change detection, no
`mark_seen_verified`, no stale sweep). Real (rolled-back) DB; fake fetch and a counting extractor."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest

from app.data.repositories import llm_budget_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringTrigger
from app.models.scholarship import LifecycleStatus
from app.models.source import SourceStatus
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor.graph import run_source_monitor

CLASSIFIED = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred", funding_status="fully_funded",
    funding_status_confidence="inferred", provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred", field="Computer Science", field_confidence="inferred",
)
STABLE_PAGE = "<html><body><ul><li>Alpha Scholarship 2027</li></ul></body></html>"


@pytest.fixture
def env(db_session_factory, monkeypatch):
    session = db_session_factory()
    state = {"extract_calls": 0, "fetch_calls": 0, "html": STABLE_PAGE, "candidates": None}

    domain = f"unchanged-{uuid.uuid4().hex[:10]}.example.com"
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Unchanged Test", source_type="gov", official_status="official", domain=domain,
            access_method="web", discovery_role=True, reliability_level="high", status="active",
            update_frequency="weekly",
        ),
    )
    session.commit()
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda db, **kw: [source_repo.get_source_by_id(db, source.id) or db.get(type(source), source.id)])
    monkeypatch.setattr("app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: CLASSIFIED)

    def fetch(url, headers=None):
        state["fetch_calls"] += 1
        return FetchResult(url=url, success=True, status_code=200, html=state["html"], final_url=url)

    def extract(html, *, source_url):
        state["extract_calls"] += 1
        candidates = state["candidates"]
        if candidates is None:
            candidates = [ExtractedListingCandidate(name="Alpha Scholarship 2027", degree_level="PhD",
                                                    funding_status="fully_funded", deadline="2027-03-01")]
        return ExtractListingOutput(candidates=candidates, accepted_count=len(candidates), rejected_count=0)

    class Env:
        pass

    e = Env()
    e.session, e.source, e.state = session, source, state

    # 003 US3: a source is only checked when due, so successive runs see a day and an hour pass (inside the
    # 7-day forced re-check, so the unchanged page is still skipped).
    clock = {"runs": 0, "start": datetime.now(timezone.utc)}

    def run(now=None):
        if now is None:
            clock["runs"] += 1
            moment = clock["start"] + timedelta(hours=25 * clock["runs"])
            now = lambda: moment  # noqa: E731
        return run_source_monitor(
            session, trigger=MonitoringTrigger.MANUAL, fetch=fetch, extract_listing=extract,
            sleep=lambda _d: None, now=now,
        )

    e.run, e.clock = run, clock
    e.stored = lambda: {s.name: s for s in scholarship_repo.list_for_source(session, source.id)}
    yield e
    session.close()


def test_two_runs_on_an_unchanged_page_make_no_extraction_on_the_second(env):
    env.run()
    assert env.state["extract_calls"] == 1
    run = env.run()
    assert env.state["extract_calls"] == 1  # zero provider requests on the second run
    assert env.state["fetch_calls"] == 2  # the page itself is still fetched
    assert (run.sources_processed, run.sources_failed, run.changes_detected) == (1, 0, 0)


def test_an_unchanged_result_touches_records_and_lifecycle_not_at_all(env):
    env.run()
    record = next(iter(env.stored().values()))
    before = (record.last_verified_at, record.lifecycle_status, record.verification_status)
    # If the sweep ran with an empty listed-names set it would look like "everything was removed".
    env.state["candidates"] = []
    env.run()
    env.session.expire_all()
    record = next(iter(env.stored().values()))
    assert (record.last_verified_at, record.lifecycle_status, record.verification_status) == before
    assert record.lifecycle_status != LifecycleStatus.CLOSED


def test_an_unchanged_check_is_a_success_for_health_and_timestamps(env):
    env.run()
    env.session.expire_all()
    first = source_repo.get_source_by_id(env.session, env.source.id)
    first_success = first.last_success_at
    run = env.run()
    env.session.expire_all()
    source = source_repo.get_source_by_id(env.session, env.source.id)
    assert source.status == SourceStatus.ACTIVE
    assert source.last_success_at > first_success and source.last_checked_at >= source.last_success_at
    assert run.sources_failed == 0


def test_a_failing_source_recovers_through_an_unchanged_check(env):
    env.run()
    source_repo.mark_source_failing(env.session, env.source.id)
    env.session.expire_all()
    env.run()
    env.session.expire_all()
    assert env.session.get(type(env.source), env.source.id).status == SourceStatus.ACTIVE


def test_the_skipped_counter_and_the_avoided_ledger_event(env, caplog):
    env.run()
    with caplog.at_level(logging.INFO):
        env.run()
    finished = [r for r in caplog.records if r.getMessage() == "monitoring run finished"]
    assert finished and finished[-1].sources_skipped == 1
    from app.core.config import settings
    from zoneinfo import ZoneInfo

    second_run_at = env.clock["start"] + timedelta(hours=25 * 2)  # the ledger day follows the run's clock
    day = second_run_at.astimezone(ZoneInfo(settings.llm_budget_timezone)).date()
    assert llm_budget_repo.count(env.session, day, "generate", "monitoring", "avoided_unchanged") == 1


def test_a_real_change_makes_exactly_one_extraction(env):
    env.run()
    env.state["html"] = "<html><body><ul><li>Alpha Scholarship 2027</li><li>Beta Grant</li></ul></body></html>"
    env.run()
    assert env.state["extract_calls"] == 2
