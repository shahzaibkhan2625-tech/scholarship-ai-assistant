"""003 T305 (data-model.md, FR-BUDGET-8): the daily retention job also prunes the LLM budget ledger.

Rows older than `LLM_LEDGER_RETENTION_DAYS` are deleted; newer rows (and the day exactly on the cutoff) and the
pause table are left alone. Safety: every call passes an explicit `today` and runs on the rolled-back session."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.data.repositories import llm_budget_repo
from app.models.llm_budget import LlmBudgetLedger, LlmBudgetState
from app.scheduling import scheduler as sched_mod
from app.services import retention

TODAY = date(2026, 10, 13)


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _days(db):
    return sorted({row.budget_day for row in db.execute(select(LlmBudgetLedger)).scalars() if row.request_type == "ret-test"})


def _seed(db):
    for offset in (1, 30, 89, 90, 91, 200, 400):
        llm_budget_repo.increment(db, TODAY - timedelta(days=offset), "ret-test", "monitoring", "allowed", by=2)
    db.commit()


def test_rows_older_than_the_window_are_deleted_and_newer_rows_kept(db):
    _seed(db)
    deleted = retention.prune_ledger(db, retention_days=90, today=TODAY)
    assert deleted >= 3  # 91, 200 and 400 days old (a shared test DB may hold other rows of its own)
    assert _days(db) == [TODAY - timedelta(days=d) for d in (90, 89, 30, 1)]  # the cutoff day itself is kept


def test_the_default_window_is_the_ledger_retention_setting(db, monkeypatch):
    _seed(db)
    monkeypatch.setattr(settings, "llm_ledger_retention_days", 30)
    retention.prune_ledger(db, today=TODAY)
    assert _days(db) == [TODAY - timedelta(days=d) for d in (30, 1)]


def test_the_pause_table_is_never_touched(db):
    llm_budget_repo.set_pause(db, "ret-test", datetime(2020, 1, 1, tzinfo=timezone.utc), "daily_quota", 20)
    _seed(db)
    retention.prune_ledger(db, retention_days=1, today=TODAY)
    assert db.get(LlmBudgetState, "ret-test") is not None


def test_a_dry_run_counts_without_deleting(db):
    _seed(db)
    before = _days(db)
    would = retention.prune_ledger(db, retention_days=90, today=TODAY, dry_run=True)
    assert would >= 3
    assert _days(db) == before


# -- the scheduler entry point ---------------------------------------------------------------------------------------


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def job(monkeypatch):
    import app.data.repositories.db as db_mod

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _Session())
    return SimpleNamespace(calls=[])


def test_the_daily_retention_job_prunes_the_ledger_as_well(job, monkeypatch):
    monkeypatch.setattr(retention, "prune_fetch_log", lambda session, **kw: job.calls.append("fetch_log") or 0)
    monkeypatch.setattr(retention, "prune_ledger", lambda session, **kw: job.calls.append("ledger") or 0)
    sched_mod._run_scheduled_retention()
    assert job.calls == ["fetch_log", "ledger"]


def test_a_failing_fetch_log_prune_does_not_stop_the_ledger_prune(job, monkeypatch):
    def boom(session, **kw):
        raise RuntimeError("cold start")

    monkeypatch.setattr(retention, "prune_fetch_log", boom)
    monkeypatch.setattr(retention, "prune_ledger", lambda session, **kw: job.calls.append("ledger") or 0)
    sched_mod._run_scheduled_retention()  # must not raise
    assert job.calls == ["ledger"]


def test_a_failing_ledger_prune_never_kills_the_job(job, monkeypatch):
    def boom(session, **kw):
        raise RuntimeError("cold start")

    monkeypatch.setattr(retention, "prune_fetch_log", lambda session, **kw: job.calls.append("fetch_log") or 0)
    monkeypatch.setattr(retention, "prune_ledger", boom)
    sched_mod._run_scheduled_retention()  # must not raise
    assert job.calls == ["fetch_log"]
