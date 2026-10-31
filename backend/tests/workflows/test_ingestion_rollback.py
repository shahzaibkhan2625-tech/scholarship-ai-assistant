"""010 US7 T418 (FR-RECOV-1/2, SC-007): a database error in an ingestion stage must leave a usable
session, so `_log_failure` can write its `source_fetch_log` row.

Started at the manual monitoring run, `POST /monitoring/runs` and the scheduler tick (the URL-match
workflow never calls `run_ingestion`; its only callers are the monitoring run and the discovery agent).
The `store` stage is exercised from all three starts; both stages from the workflow function. Two stages: `store` (non-retryable path,
`_run_stage`'s own `except`) and `dedup` (retryable path, `run_with_retry` returns the last error).
The injected fault is a statement that fails inside Postgres (`SELECT 1/0`), which really aborts the
transaction. Real (rolled-back) DB."""

import functools
import uuid

import pytest
from sqlalchemy import text

from app.data.repositories import monitoring_repo, source_repo
from app.models.monitoring import MonitoringStatus, MonitoringTrigger
from app.models.source import FetchStatus
from app.schemas.source import SourceRegistryCreate
from app.scheduling import scheduler
from app.services.classify import ClassifiedScholarshipFields
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.ingestion import graph as ingestion_graph
from app.workflows.source_monitor.graph import run_source_monitor

_FAKE_CLASSIFICATION = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred",
    funding_status="fully_funded", funding_status_confidence="inferred",
    provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred",
    field="Computer Science", field_confidence="inferred",
)


@pytest.fixture
def env(db_session_factory, monkeypatch):
    session = db_session_factory()
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Ingestion Rollback", source_type="gov", official_status="official",
            domain=f"ingest-{uuid.uuid4().hex[:8]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active",
        ),
    )
    real = source_repo.get_monitorable_sources
    monkeypatch.setattr(
        source_repo, "get_monitorable_sources", lambda db, *a, **k: [s for s in real(db, *a, **k) if s.id == source.id]
    )
    monkeypatch.setattr(
        "app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text_: _FAKE_CLASSIFICATION
    )
    yield session, source
    session.close()


def _listing():
    return ExtractListingOutput(
        candidates=[
            ExtractedListingCandidate(
                name=f"Rollback Scholarship {uuid.uuid4().hex[:6]}", degree_level="PhD",
                funding_status="fully_funded", deadline="2027-03-01",
            )
        ],
        accepted_count=1,
        rejected_count=0,
    )


def _fetch(url):
    return FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>")


def _run(session):
    listing = _listing()
    return run_source_monitor(
        session,
        trigger=MonitoringTrigger.MANUAL,
        fetch=_fetch,
        extract_listing=lambda html, *, source_url: listing,
        sleep=lambda _delay: None,
    )


def _seam_the_workflow(monkeypatch, db_session_factory):
    """The route and the tick call `run_source_monitor` without fetch seams; give both the fakes."""
    listing = _listing()
    seamed = functools.partial(
        run_source_monitor, fetch=_fetch, extract_listing=lambda html, *, source_url: listing, sleep=lambda _d: None
    )
    monkeypatch.setattr("app.workflows.source_monitor.graph.run_source_monitor", seamed)
    monkeypatch.setattr("app.api.monitoring.run_source_monitor", seamed)
    # background bodies open their own sessions: hand them the test's rolled-back connection
    monkeypatch.setattr("app.api.monitoring.SessionLocal", db_session_factory)
    monkeypatch.setattr("app.data.repositories.db.SessionLocal", db_session_factory)


def _failure_rows(session, source, stage):
    session.expire_all()
    return [
        log for log in source_repo.get_fetch_logs_for_source(session, source.id)
        if log.status == FetchStatus.FAIL and (log.error or "").startswith(f"[{stage}]")
    ]


def test_database_error_in_the_store_stage_still_logs_the_failure(env, monkeypatch):
    session, source = env
    monkeypatch.setattr(ingestion_graph, "_validate_required_fields", lambda raw: session.execute(text("SELECT 1/0")))

    run = _run(session)

    assert run.status == MonitoringStatus.COMPLETED
    assert len(_failure_rows(session, source, "store")) == 1  # written by _log_failure, not by the crash handler
    assert _failure_rows(session, source, "monitor") == []  # the source did not crash out of the stage


def test_database_error_in_a_retryable_stage_still_logs_the_failure(env, monkeypatch):
    session, source = env
    monkeypatch.setattr(ingestion_graph, "_db_backed_existing", lambda db, name: db.execute(text("SELECT 1/0")))

    run = _run(session)

    assert run.status == MonitoringStatus.COMPLETED
    assert len(_failure_rows(session, source, "dedup")) == 1
    assert _failure_rows(session, source, "monitor") == []


def _fail_the_store_stage(monkeypatch, session):
    monkeypatch.setattr(ingestion_graph, "_validate_required_fields", lambda raw: session.execute(text("SELECT 1/0")))


def test_the_manual_run_route_logs_a_store_stage_database_error(
    env, client, make_user, db_session_factory, monkeypatch
):
    import app.api.monitoring as monitoring_api

    session, source = env
    _fail_the_store_stage(monkeypatch, session)
    _seam_the_workflow(monkeypatch, db_session_factory)
    operator = make_user(is_operator=True)
    started: list[uuid.UUID] = []
    real_execute_run = monitoring_api._execute_run
    monkeypatch.setattr(monitoring_api, "_execute_run", started.append)  # run the real body after the response

    response = client.post("/monitoring/runs", headers=operator["headers"])  # first call: the route

    assert response.status_code == 202
    (run_id,) = started
    real_execute_run(run_id)
    assert monitoring_repo.get_run(session, run_id).status == MonitoringStatus.COMPLETED
    assert len(_failure_rows(session, source, "store")) == 1
    assert _failure_rows(session, source, "monitor") == []


def test_the_scheduler_tick_logs_a_store_stage_database_error(env, db_session_factory, monkeypatch):
    session, source = env
    _fail_the_store_stage(monkeypatch, session)
    _seam_the_workflow(monkeypatch, db_session_factory)

    scheduler._run_scheduled_monitor()  # first call: the tick

    (run,) = monitoring_repo.list_runs(session, limit=1)
    assert run.trigger == MonitoringTrigger.SCHEDULED and run.status == MonitoringStatus.COMPLETED
    assert len(_failure_rows(session, source, "store")) == 1
    assert _failure_rows(session, source, "monitor") == []
