"""010 US7 T416 (FR-RECOV-1/2, SC-007): a database error inside candidate detection, reached from a
manual monitoring run, must not leave the session in an aborted transaction.

The broad `except Exception: pass` in `official_fetch._detect_and_record_candidate_sources` used to
swallow the error without a rollback, so the very next statement on the same session failed and the
source was recorded as crashed. Real (rolled-back) DB: the injected fault runs a statement that fails
inside Postgres (`SELECT 1/0`), which genuinely aborts the transaction."""

import logging
import uuid

import pytest
from sqlalchemy import text

from app.data.repositories import monitoring_repo, source_repo
from app.models.monitoring import MonitoringStatus, MonitoringTrigger
from app.models.source import FetchStatus
from app.schemas.source import SourceRegistryCreate
from app.sources.connectors import official_fetch
from app.tools.detect_candidate_links import CandidateLinkSignal
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor.graph import run_source_monitor


@pytest.fixture
def two_sources(db_session_factory, monkeypatch):
    session = db_session_factory()
    uid = uuid.uuid4().hex[:8]
    sources = [
        source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name=f"Detection Rollback {label}", source_type="gov", official_status="official",
                domain=f"detect-{label}-{uid}.example.com", access_method="web", discovery_role=True,
                reliability_level="high", status="active",
            ),
        )
        for label in ("a", "b")
    ]
    ids = {source.id for source in sources}
    real = source_repo.get_monitorable_sources
    monkeypatch.setattr(
        source_repo, "get_monitorable_sources", lambda db, *a, **k: [s for s in real(db, *a, **k) if s.id in ids]
    )
    yield session, sources
    session.close()


def _fail_inside_postgres(db, **kwargs):
    db.execute(text("SELECT 1/0"))  # raises DataError and aborts the transaction


def _run(session):
    return run_source_monitor(
        session,
        trigger=MonitoringTrigger.MANUAL,
        fetch=lambda url: FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>"),
        extract_listing=lambda html, *, source_url: ExtractListingOutput(
            candidates=[
                ExtractedListingCandidate(
                    name="Detection Rollback Scholarship", degree_level="PhD",
                    funding_status="fully_funded", deadline="2027-03-01",
                )
            ],
            accepted_count=1,
            rejected_count=0,
        ),
        sleep=lambda _delay: None,
    )


def test_a_database_error_in_candidate_detection_does_not_fail_the_source_or_the_run(
    two_sources, monkeypatch, caplog
):
    session, sources = two_sources
    signal = CandidateLinkSignal(
        domain="newly-seen.example.org", url="https://newly-seen.example.org/", matched_keyword="scholarship"
    )
    monkeypatch.setattr(official_fetch, "find_candidate_links", lambda *a, **k: [signal])
    monkeypatch.setattr(source_repo, "record_or_bump_candidate_source", _fail_inside_postgres)
    monkeypatch.setattr("app.workflows.source_monitor.graph.run_ingestion", lambda *a, **k: {})  # not under test

    with caplog.at_level(logging.WARNING):
        run = _run(session)

    session.expire_all()
    assert run.status == MonitoringStatus.COMPLETED
    assert (run.sources_processed, run.sources_failed) == (2, 0)  # the next source was still processed
    for source in sources:
        fresh = source_repo.get_source_by_id(session, source.id)
        assert fresh.last_success_at is not None  # the fetch outcome was recorded after the failed detection
        logs = monitoring_repo.get_fetch_logs_for_run(session, run.id)
        assert any(log.source_id == source.id and log.status == FetchStatus.OK for log in logs)
    # the failure is visible (type only), not silent
    assert "candidate detection failed" in caplog.text and "DataError" in caplog.text
    assert "division by zero" not in caplog.text
