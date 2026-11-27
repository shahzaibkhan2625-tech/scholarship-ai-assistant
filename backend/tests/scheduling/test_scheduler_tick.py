"""003 T296 / T300 (FR-CADENCE-1, FR-OBS-3, plan decision 5): the cheap scheduler tick.

A scheduled tick first asks "is anything due, or does any candidate need validation?". If not it returns
without creating a `monitoring_runs` row and logs exactly one structured line; either way it logs one line
with the `due`, `checked`, `skipped` and `deferred` counts. The classify drain keeps its turn after every
tick, and a database error in an idle or a busy tick is rolled back and never kills the scheduler thread.
Real (rolled-back) DB for the idle/busy selection; no network, no provider."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.data.repositories import source_repo
from app.models.monitoring import MonitoringRun
from app.schemas.source import SourceRegistryCreate
from app.scheduling import scheduler as sched_mod
from app.services import classify_backfill
from app.workflows.source_monitor import graph as monitor_graph


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


@pytest.fixture
def tick(session, monkeypatch):
    """The scheduler's own entry point on the test session; the drain is replaced by a recorder."""
    import app.data.repositories.db as db_mod

    class _Ctx:
        def __enter__(self_inner):
            return session

        def __exit__(self_inner, *a):
            return False

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _Ctx())
    calls = SimpleNamespace(drain=0, monitor=0, rollbacks=0)
    monkeypatch.setattr(classify_backfill, "drain_classify_pending", lambda s, **kw: setattr(calls, "drain", calls.drain + 1))
    real_rollback = session.rollback

    def counting_rollback():
        calls.rollbacks += 1
        real_rollback()

    monkeypatch.setattr(session, "rollback", counting_rollback)
    return calls


def _source(session, label, *, frequency="weekly", checked_ago: timedelta | None = None):
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name=f"Tick {label}", source_type="gov", official_status="official",
            domain=f"tick-{label}-{uuid.uuid4().hex[:8]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active", update_frequency=frequency,
        ),
    )
    if checked_ago is not None:
        source.last_checked_at = datetime.now(timezone.utc) - checked_ago
    session.commit()
    return source


def _only(monkeypatch, *sources):
    monkeypatch.setattr(
        source_repo, "get_monitorable_sources", lambda db, **kw: [db.get(type(s), s.id) for s in sources]
    )


def _runs(session) -> int:
    return session.execute(select(func.count()).select_from(MonitoringRun)).scalar_one()


def _tick_lines(caplog):
    return [r for r in caplog.records if r.getMessage() == "monitoring tick"]


def test_an_idle_tick_creates_no_run_row_and_logs_exactly_one_line(session, tick, monkeypatch, caplog):
    quiet = _source(session, "quiet", checked_ago=timedelta(hours=1))
    _only(monkeypatch, quiet)
    monkeypatch.setattr(monitor_graph, "run_source_monitor", lambda *a, **k: pytest.fail("an idle tick must not run"))
    monkeypatch.setattr(monitor_graph.candidate_validation_repo, "list_pending_needing_validation", lambda db, **kw: [])
    before = _runs(session)
    with caplog.at_level(logging.INFO):
        sched_mod._run_scheduled_monitor()
    assert _runs(session) == before
    [line] = _tick_lines(caplog)
    assert (line.due, line.checked, line.skipped, line.deferred) == (0, 0, 0, 0)
    assert tick.drain == 1  # the classify drain keeps its turn after an idle tick


def test_a_tick_with_a_due_source_runs_and_logs_its_counts(session, tick, monkeypatch, caplog):
    due = _source(session, "due", checked_ago=timedelta(days=3))
    _only(monkeypatch, due)

    def fake_run(db, **kw):
        assert kw["trigger"].value == "scheduled"
        return SimpleNamespace(sources_processed=1, sources_skipped=1, sources_deferred=0)

    monkeypatch.setattr(monitor_graph, "run_source_monitor", fake_run)
    with caplog.at_level(logging.INFO):
        sched_mod._run_scheduled_monitor()
    [line] = _tick_lines(caplog)
    assert (line.due, line.checked, line.skipped, line.deferred) == (1, 1, 1, 0)
    assert tick.drain == 1


def test_a_pending_candidate_alone_makes_the_tick_run(session, tick, monkeypatch, caplog):
    quiet = _source(session, "quiet2", checked_ago=timedelta(hours=1))
    _only(monkeypatch, quiet)
    monkeypatch.setattr(
        monitor_graph.candidate_validation_repo, "list_pending_needing_validation", lambda db, **kw: [uuid.uuid4()]
    )
    ran = []
    monkeypatch.setattr(
        monitor_graph, "run_source_monitor",
        lambda db, **kw: ran.append(1) or SimpleNamespace(sources_processed=0, sources_skipped=0, sources_deferred=0),
    )
    sched_mod._run_scheduled_monitor()
    assert ran == [1]


def test_a_source_gated_by_next_attempt_at_is_not_due(session, tick, monkeypatch, caplog):
    from app.data.repositories import check_state_repo

    gated = _source(session, "gated", checked_ago=timedelta(days=30))
    check_state_repo.update(session, gated.id, next_attempt_at=datetime.now(timezone.utc) + timedelta(hours=1))
    session.commit()
    _only(monkeypatch, gated)
    monkeypatch.setattr(monitor_graph.candidate_validation_repo, "list_pending_needing_validation", lambda db, **kw: [])
    monkeypatch.setattr(monitor_graph, "run_source_monitor", lambda *a, **k: pytest.fail("gated source ran"))
    with caplog.at_level(logging.INFO):
        sched_mod._run_scheduled_monitor()
    assert len(_tick_lines(caplog)) == 1


def _db_error():
    return OperationalError("select 1", {}, Exception("connection lost"))


def test_a_database_error_in_an_idle_tick_is_rolled_back_and_the_drain_still_runs(session, tick, monkeypatch):
    def boom(db, now=None):
        raise _db_error()

    monkeypatch.setattr(monitor_graph, "due_work", boom)
    sched_mod._run_scheduled_monitor()  # must not raise
    assert tick.rollbacks >= 1
    assert tick.drain == 1


def test_a_database_error_in_a_busy_tick_is_rolled_back_and_the_drain_still_runs(session, tick, monkeypatch):
    monkeypatch.setattr(
        monitor_graph, "due_work", lambda db, now=None: monitor_graph.DueWork(due=2, candidates=0)
    )

    def boom(db, **kw):
        raise _db_error()

    monkeypatch.setattr(monitor_graph, "run_source_monitor", boom)
    sched_mod._run_scheduled_monitor()  # must not raise
    assert tick.rollbacks >= 1
    assert tick.drain == 1


def test_the_tick_calls_the_monitor_with_the_scheduled_trigger_and_default_alert_step(session, tick, monkeypatch):
    """The email alert step lives inside `run_source_monitor` and is left at its defaults by the tick."""
    monkeypatch.setattr(monitor_graph, "due_work", lambda db, now=None: monitor_graph.DueWork(due=1, candidates=0))
    seen = {}
    monkeypatch.setattr(
        monitor_graph, "run_source_monitor",
        lambda db, **kw: seen.update(kw) or SimpleNamespace(sources_processed=1, sources_skipped=0, sources_deferred=0),
    )
    sched_mod._run_scheduled_monitor()
    assert list(seen) == ["trigger"]
