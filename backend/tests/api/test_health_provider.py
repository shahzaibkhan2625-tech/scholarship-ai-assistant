"""003 T288 (FR-ERR-2/5, R-10, contracts/api-delta.md section 2): the health routes show provider-side failures
separately from `consecutive_failures`, and the fetch log exposes `outcome`. Real (rolled-back) DB."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import FetchStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate

BASE = datetime(2027, 4, 1, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


@pytest.fixture
def operator_user(client, make_user):
    return {"client": client, "headers": make_user(is_operator=True)["headers"]}


def _source(session):
    return source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="HP", source_type="gov", official_status="official",
            domain=f"hp-{uuid.uuid4().hex[:8]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active",
        ),
    )


def _run_with(session, source, minute, status, **kw):
    run = MonitoringRun(trigger=MonitoringTrigger.SCHEDULED, started_at=BASE + timedelta(minutes=minute))
    session.add(run)
    session.commit()
    for offset, row_status, extra in ((0, FetchStatus.OK, {}), (1, status, kw)):
        source_repo.log_fetch(
            session,
            SourceFetchLogCreate(
                source_id=source.id, status=row_status, monitoring_run_id=run.id,
                started_at=BASE + timedelta(minutes=minute, seconds=offset), **extra,
            ),
        )


def test_health_reports_provider_failures_apart_from_consecutive_failures(authed_user, session):
    source = _source(session)
    _run_with(session, source, 10, FetchStatus.FAIL, error="site down")
    _run_with(session, source, 20, FetchStatus.FAIL, outcome="provider_quota", error="provider quota")
    _run_with(session, source, 30, FetchStatus.FAIL, outcome="provider_error", error="provider error")

    response = authed_user["client"].get("/sources/health", headers=authed_user["headers"])
    row = next(r for r in response.json() if r["source_id"] == str(source.id))
    assert row["consecutive_failures"] == 1
    assert row["recent_provider_failures"] == 2
    assert row["last_provider_failure_at"].startswith("2027-04-01T00:30:01")


def test_a_source_without_provider_failures_reports_zero_and_null(authed_user, session):
    source = _source(session)
    _run_with(session, source, 10, FetchStatus.OK, items_found=3)
    response = authed_user["client"].get("/sources/health", headers=authed_user["headers"])
    row = next(r for r in response.json() if r["source_id"] == str(source.id))
    assert row["recent_provider_failures"] == 0 and row["last_provider_failure_at"] is None


def test_fetch_log_entries_expose_the_outcome(operator_user, session):
    source = _source(session)
    _run_with(session, source, 10, FetchStatus.FAIL, outcome="provider_parse", error="provider parse")
    _run_with(session, source, 20, FetchStatus.OK, items_found=2)
    response = operator_user["client"].get(f"/sources/{source.id}/fetch-log", headers=operator_user["headers"])
    assert response.status_code == 200
    entries = response.json()
    assert all("outcome" in entry for entry in entries)
    assert {e["outcome"] for e in entries} == {"provider_parse", None}
