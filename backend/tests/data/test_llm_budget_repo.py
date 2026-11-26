"""003 T256 (ledger half): llm_budget_repo against the per-test rollback fixture. In CI the tests
run as `scholarship_app`, so they also prove default privileges cover the new tables."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.data.repositories import llm_budget_repo as repo

DAY = date(2026, 11, 26)


@pytest.fixture
def db(db_session_factory):
    with db_session_factory() as session:
        yield session


def test_increment_is_atomic_count_plus_one(db):
    repo.increment(db, DAY, "generate", "monitoring", "allowed")
    repo.increment(db, DAY, "generate", "monitoring", "allowed")
    repo.increment(db, DAY, "generate", "monitoring", "allowed", by=3)
    assert repo.count(db, DAY, "generate", "monitoring", "allowed") == 5


def test_sums_per_class_and_per_event(db):
    repo.increment(db, DAY, "generate", "user_facing", "allowed")
    repo.increment(db, DAY, "generate", "monitoring", "allowed", by=2)
    repo.increment(db, DAY, "generate", "monitoring", "deferred", by=4)
    repo.increment(db, DAY, "embed", "user_facing", "allowed", by=7)
    assert repo.sums_by_class(db, DAY, "generate", "allowed") == {"user_facing": 1, "monitoring": 2}
    assert repo.sums_by_class(db, DAY, "generate", "deferred") == {"monitoring": 4}
    assert repo.allowed_today(db, DAY, "generate") == 3
    assert repo.allowed_today(db, DAY, "embed") == 7
    assert repo.allowed_today(db, DAY + timedelta(days=1), "generate") == 0


def test_day_counts_returns_every_row_of_the_day(db):
    repo.increment(db, DAY, "generate", "monitoring", "deferred", by=2)
    repo.increment(db, DAY, "generate", "monitoring", "avoided_unchanged")
    assert repo.day_counts(db, DAY) == {
        ("generate", "monitoring", "deferred"): 2,
        ("generate", "monitoring", "avoided_unchanged"): 1,
    }


def test_pause_set_get_clear(db):
    assert repo.get_pause(db, "generate") is None
    until = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    repo.set_pause(db, "generate", until, "daily_quota", observed_limit=20)
    pause = repo.get_pause(db, "generate")
    assert (pause.paused_until, pause.pause_reason, pause.observed_limit) == (until, "daily_quota", 20)
    assert pause.pause_set_at is not None
    repo.set_pause(db, "generate", until + timedelta(hours=1), "rate_limit")  # replaces, same row
    assert repo.get_pause(db, "generate").pause_reason == "rate_limit"
    repo.clear_pause(db, "generate")
    assert repo.get_pause(db, "generate").paused_until is None


def test_prune_removes_only_rows_older_than_the_cutoff(db):
    repo.increment(db, DAY - timedelta(days=100), "generate", "monitoring", "allowed")
    repo.increment(db, DAY - timedelta(days=10), "generate", "monitoring", "allowed")
    repo.increment(db, DAY, "generate", "monitoring", "allowed")
    repo.set_pause(db, "generate", datetime(2026, 11, 27, tzinfo=timezone.utc), "daily_quota")
    assert repo.prune_older_than(db, DAY - timedelta(days=90)) == 1
    assert repo.count(db, DAY - timedelta(days=100), "generate", "monitoring", "allowed") == 0
    assert repo.count(db, DAY - timedelta(days=10), "generate", "monitoring", "allowed") == 1
    assert repo.get_pause(db, "generate") is not None


def test_advisory_lock_helper_can_be_taken_twice_in_one_transaction(db):
    repo.advisory_lock(db, DAY, "generate")
    repo.advisory_lock(db, DAY, "generate")  # re-entrant for the same session
