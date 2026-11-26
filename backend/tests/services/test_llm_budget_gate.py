"""003 T266: the database-backed gate. Single-transaction tests inject the rollback session factory so
the gate's own short transaction never writes to the shared database; the concurrency test needs real
commits, so it uses a far-future budget day and removes its rows afterwards."""

import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete

from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmProviderError,
    LlmQuotaExhausted,
    LlmTemporarilyUnavailable,
    Priority,
    RequestType,
    llm_priority,
)
from app.data.repositories import llm_budget_repo as repo
from app.services.llm_budget import BudgetConfig, DbLlmGate, next_boundary

LA = ZoneInfo("America/Los_Angeles")
GEN = RequestType.GENERATE


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _config(allowance: int = 6, reserve: int = 2) -> BudgetConfig:
    return BudgetConfig(
        allowance={RequestType.GENERATE: allowance, RequestType.EMBED: None},
        reserve=reserve,
        offsets={Priority.BACKFILL: 0, Priority.MONITORING: 1, Priority.VALIDATION: 2},
        backfill_share=2,
        tz=LA,
        default_pause_seconds=900,
    )


@pytest.fixture
def now():
    return datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_gate(db_session_factory, now):
    clock = Clock(now)

    def _make(**kwargs):
        return DbLlmGate(kwargs.pop("config", _config()), session_factory=db_session_factory, clock=clock, **kwargs)

    _make.clock = clock
    return _make


def _day(now):
    return now.astimezone(LA).date()


def test_counts_are_kept_per_class_and_event(make_gate, db_session_factory, now):
    gate = make_gate()
    gate.acquire(GEN, Priority.USER_FACING)
    gate.acquire(GEN, Priority.MONITORING)
    gate.acquire(RequestType.EMBED, Priority.USER_FACING)
    with db_session_factory() as db:
        assert repo.count(db, _day(now), "generate", "user_facing", "allowed") == 1
        assert repo.count(db, _day(now), "generate", "monitoring", "allowed") == 1
        assert repo.count(db, _day(now), "embed", "user_facing", "allowed") == 1


def test_deferred_and_denied_events_are_recorded(make_gate, db_session_factory, now):
    gate = make_gate()
    for _ in range(4):  # allowance 6, reserve 2 -> monitoring (offset 1) stops once remaining <= 3
        gate.acquire(GEN, Priority.USER_FACING)
    with pytest.raises(LlmBudgetDeferred) as deferred:
        gate.acquire(GEN, Priority.MONITORING)
    assert deferred.value.retry_at == next_boundary(now, LA)
    gate.acquire(GEN, Priority.USER_FACING)
    gate.acquire(GEN, Priority.USER_FACING)
    with pytest.raises(LlmTemporarilyUnavailable) as denied:
        gate.acquire(GEN, Priority.USER_FACING)
    assert denied.value.reason == "daily_allowance_spent"
    with db_session_factory() as db:
        assert repo.count(db, _day(now), "generate", "monitoring", "deferred") == 1
        assert repo.count(db, _day(now), "generate", "user_facing", "denied") == 1
        assert repo.allowed_today(db, _day(now), "generate") == 6  # refusals are not counted as used


def test_provider_429_sets_the_pause_and_records_a_discrepancy(make_gate, db_session_factory, now):
    gate = make_gate()
    gate.acquire(GEN, Priority.USER_FACING)  # local count 1, below the allowance of 6
    until = now + timedelta(hours=3)
    with llm_priority(Priority.USER_FACING):
        gate.report_provider_error(GEN, LlmQuotaExhausted(until, daily=True, observed_limit=20))
    with db_session_factory() as db:
        pause = repo.get_pause(db, "generate")
        assert pause.paused_until == until and pause.pause_reason == "daily_quota" and pause.observed_limit == 20
        assert repo.count(db, _day(now), "generate", "user_facing", "provider_exhausted") == 1
        assert repo.count(db, _day(now), "generate", "user_facing", "discrepancy") == 1
    with pytest.raises(LlmTemporarilyUnavailable) as refused:
        gate.acquire(GEN, Priority.USER_FACING)
    assert refused.value.reason == "provider_quota" and refused.value.retry_at == until
    with pytest.raises(LlmBudgetDeferred):
        gate.acquire(GEN, Priority.MONITORING)
    make_gate.clock.now = until + timedelta(seconds=1)  # pause over
    gate.acquire(GEN, Priority.USER_FACING)


def test_no_discrepancy_when_the_local_count_already_reached_the_allowance(make_gate, db_session_factory, now):
    gate = make_gate(config=_config(allowance=3, reserve=1))
    for _ in range(3):
        gate.acquire(GEN, Priority.USER_FACING)
    with llm_priority(Priority.USER_FACING):
        gate.report_provider_error(GEN, LlmQuotaExhausted(now + timedelta(hours=1), daily=True))
    with db_session_factory() as db:
        assert repo.count(db, _day(now), "generate", "user_facing", "discrepancy") == 0


def test_non_quota_provider_errors_do_not_pause(make_gate, db_session_factory):
    gate = make_gate()
    gate.report_provider_error(GEN, LlmProviderError())
    with db_session_factory() as db:
        assert repo.get_pause(db, "generate") is None


def test_a_new_gate_instance_sees_the_same_counts(make_gate, db_session_factory, now):
    first = make_gate()
    for _ in range(6):
        first.acquire(GEN, Priority.USER_FACING)
    second = make_gate()  # restart / second worker
    with pytest.raises(LlmTemporarilyUnavailable):
        second.acquire(GEN, Priority.USER_FACING)


def test_a_new_budget_day_starts_from_zero(make_gate):
    gate = make_gate()
    for _ in range(6):
        gate.acquire(GEN, Priority.USER_FACING)
    make_gate.clock.now = make_gate.clock.now + timedelta(days=1)
    gate.acquire(GEN, Priority.USER_FACING)


class _BrokenFactory:
    def __call__(self):
        raise RuntimeError("budget store unreachable")


def test_store_failure_defers_background_and_lets_students_proceed_and_counts_the_failure(now):
    gate = DbLlmGate(_config(), session_factory=_BrokenFactory(), clock=Clock(now))
    with pytest.raises(LlmBudgetDeferred):
        gate.acquire(GEN, Priority.MONITORING)
    with pytest.raises(LlmBudgetDeferred):
        gate.acquire(GEN, Priority.VALIDATION)
    gate.acquire(GEN, Priority.USER_FACING)  # proceeds to the provider's own limit
    assert gate.store_failures == 3


def test_concurrent_acquisitions_never_exceed_the_allowance():
    """Real commits (the gate uses its own sessions), against the local database only."""
    from tests.conftest import _require_db

    session_local = _require_db()
    far_future = datetime(2099, 6, 1, 18, 0, tzinfo=timezone.utc)
    day = far_future.astimezone(LA).date()
    gate = DbLlmGate(_config(allowance=5, reserve=1), session_factory=session_local, clock=lambda: far_future)
    admitted, refused = [], []

    def worker():
        try:
            gate.acquire(GEN, Priority.USER_FACING)
            admitted.append(1)
        except LlmTemporarilyUnavailable:
            refused.append(1)

    try:
        threads = [threading.Thread(target=worker) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with session_local() as db:
            assert repo.allowed_today(db, day, "generate") == 5
        assert len(admitted) == 5 and len(refused) == 7
    finally:
        from app.models.llm_budget import LlmBudgetLedger

        with session_local() as db:
            db.execute(delete(LlmBudgetLedger).where(LlmBudgetLedger.budget_day == day))
            db.commit()
