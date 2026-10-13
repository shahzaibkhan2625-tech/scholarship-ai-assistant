"""US8 B2: deterministic ordering on timestamp ties (created-at-desc audit).

Rows written in one transaction share a server `now()`; a random-UUID `id`
tiebreak makes the pick STABLE, not semantically "correct" -- so these tests pin
determinism (tie -> id order), not business meaning. DB tests run on the
rolled-back session; list-endpoint ordering that needs heavy fixtures (alerts,
matches) is pinned by compiling the statement the repo issues.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.data.repositories import alert_repo, match_repo, monitoring_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import FetchStatus, SourceFetchLog
from app.schemas.source import SourceRegistryCreate
from app.services.coverage import FetchLogSnapshot, SourceSnapshot, compute_coverage_summary

TIE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _source(db):
    return source_repo.upsert_source_by_domain(
        db,
        SourceRegistryCreate(
            name="Tie Test", source_type="gov", official_status="official",
            domain=f"tie-{uuid.uuid4().hex[:12]}.example.com", access_method="web", reliability_level="high",
        ),
    )


def _run(db, started_at=None) -> MonitoringRun:
    run = MonitoringRun(trigger=MonitoringTrigger.MANUAL)
    if started_at is not None:
        run.started_at = started_at
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _tied_log(db, source, run=None, status=FetchStatus.OK) -> SourceFetchLog:
    row = SourceFetchLog(
        source_id=source.id, started_at=TIE, status=status, monitoring_run_id=run.id if run else None
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- source_repo ---------------------------------------------------------------


def test_get_fetch_logs_for_source_ties_break_by_id_desc(db):
    source = _source(db)
    rows = [_tied_log(db, source) for _ in range(5)]
    assert [r.id for r in source_repo.get_fetch_logs_for_source(db, source.id)] == sorted(
        (r.id for r in rows), reverse=True
    )


def test_ranked_runs_tie_breaks_by_run_id_desc_and_streak_is_deterministic(db):
    fail_newer, ok_older = _source(db), _source(db)
    runs = sorted((_run(db) for _ in range(2)), key=lambda r: r.id)
    low, high = runs  # same timestamps below; `high` ranks newer
    # fail_newer: the HIGH-id run failed -> it ranks first -> streak 1
    _tied_log(db, fail_newer, high, FetchStatus.FAIL)
    _tied_log(db, fail_newer, low, FetchStatus.OK)
    # ok_older: the HIGH-id run is OK -> it ranks first -> streak 0
    _tied_log(db, ok_older, high, FetchStatus.OK)
    _tied_log(db, ok_older, low, FetchStatus.FAIL)
    assert source_repo.consecutive_failure_count(db, fail_newer.id) == 1
    assert source_repo.consecutive_failure_count(db, ok_older.id) == 0


# --- monitoring_repo -----------------------------------------------------------


def test_list_runs_ties_break_by_id_desc(db):
    future = datetime(2100, 1, 1, tzinfo=timezone.utc)  # sorts above any real run
    runs = [_run(db, future) for _ in range(4)]
    listed = monitoring_repo.list_runs(db, limit=4)
    assert [r.id for r in listed] == sorted((r.id for r in runs), reverse=True)


def test_get_fetch_logs_for_run_ties_break_by_id_asc(db):
    source = _source(db)
    run = _run(db)
    rows = [_tied_log(db, source, run) for _ in range(5)]
    assert [r.id for r in monitoring_repo.get_fetch_logs_for_run(db, run.id)] == sorted(r.id for r in rows)


# --- alert_repo / match_repo (statement shape) ---------------------------------


class _CaptureDb:
    def __init__(self):
        self.stmt = None

    def execute(self, stmt):
        self.stmt = stmt

        class _R:
            def scalars(self):
                return self

            def all(self):
                return []

        return _R()

    def sql(self) -> str:
        return " ".join(str(self.stmt.compile(dialect=postgresql.dialect())).split())


def test_alert_list_for_user_orders_by_created_at_then_id_desc():
    cap = _CaptureDb()
    alert_repo.list_for_user(cap, uuid.uuid4())
    assert "ORDER BY alerts.created_at DESC, alerts.id DESC" in cap.sql()


def test_match_list_for_user_orders_by_created_at_then_id_desc():
    cap = _CaptureDb()
    match_repo.list_for_user(cap, uuid.uuid4())
    assert "ORDER BY matches.created_at DESC, matches.id DESC" in cap.sql()


# --- coverage (pure) -----------------------------------------------------------


def test_coverage_latest_outcome_is_independent_of_input_order_on_a_tie():
    source_id = uuid.uuid4()
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    a = FetchLogSnapshot(source_id=source_id, status="ok", started_at=TIE, monitoring_run_id=run_a, id=uuid.UUID(int=1))
    b = FetchLogSnapshot(source_id=source_id, status="fail", started_at=TIE, monitoring_run_id=run_b, id=uuid.UUID(int=2))
    sources = [SourceSnapshot(id=source_id, country="DE", source_type="gov", status="active")]
    outcomes = {
        compute_coverage_summary(sources, order, now=TIE + timedelta(days=1)).sources_failed
        for order in ([a, b], [b, a])
    }
    assert outcomes == {1}  # higher id = latest = the failed run, whichever order the rows arrive in
