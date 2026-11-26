"""003 T469 (FR-BUDGET-13): the pending-classification queue repository. Real (rolled-back) DB."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.data.repositories import classify_pending_repo as repo
from app.models.classify_pending import ClassifyPending
from app.models.scholarship import Scholarship

T0 = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(db_session_factory):
    with db_session_factory() as session:
        yield session


def _scholarship(db, label="q"):
    row = Scholarship(name=f"Queue {label} {uuid.uuid4().hex[:6]}", official_scholarship_url="https://x.example.com")
    db.add(row)
    db.flush()
    return row


def _queue(db, scholarship, minutes, reason="budget_deferred"):
    row = repo.enqueue(db, scholarship.id, reason)
    row.queued_at = T0 + timedelta(minutes=minutes)
    db.flush()
    return row


def test_enqueue_creates_one_row_with_the_reason_and_zero_attempts(db):
    s = _scholarship(db)
    row = repo.enqueue(db, s.id, "provider_quota")
    assert (row.scholarship_id, row.attempts, row.last_reason, row.last_attempt_at) == (s.id, 0, "provider_quota", None)
    assert row.queued_at is not None
    assert repo.size(db) >= 1


def test_enqueue_is_idempotent_per_record_and_keeps_the_original_queue_time(db):
    s = _scholarship(db)
    first = _queue(db, s, 0, "budget_deferred")
    again = repo.enqueue(db, s.id, "provider_error")
    assert again.scholarship_id == first.scholarship_id
    assert again.queued_at == T0  # still the first queue time: oldest-first order is not reset
    rows = db.execute(select(ClassifyPending).where(ClassifyPending.scholarship_id == s.id)).scalars().all()
    assert len(rows) == 1 and rows[0].last_reason == "provider_error"


def test_oldest_first_read_with_a_deterministic_tiebreak(db):
    a, b, c = _scholarship(db, "a"), _scholarship(db, "b"), _scholarship(db, "c")
    _queue(db, b, 5)
    _queue(db, a, 1)
    _queue(db, c, 1)  # same queue time as `a`: the id breaks the tie
    mine = {a.id, b.id, c.id}
    ordered = [row.scholarship_id for row in repo.list_oldest(db, limit=50) if row.scholarship_id in mine]
    tied = sorted([a.id, c.id])
    assert ordered == [*tied, b.id]


def test_list_oldest_respects_the_limit(db):
    items = [_scholarship(db, str(i)) for i in range(4)]
    for i, s in enumerate(items):
        _queue(db, s, i)
    assert len(repo.list_oldest(db, limit=2)) == 2


def test_record_attempt_increments_and_sets_reason_and_time(db):
    s = _scholarship(db)
    _queue(db, s, 0)
    repo.record_attempt(db, s.id, "provider_quota", now=T0 + timedelta(hours=1))
    row = repo.record_attempt(db, s.id, "provider_error", now=T0 + timedelta(hours=2))
    assert row.attempts == 2
    assert row.last_reason == "provider_error" and row.last_attempt_at == T0 + timedelta(hours=2)


def test_record_attempt_on_a_missing_row_is_a_no_op(db):
    assert repo.record_attempt(db, uuid.uuid4(), "provider_error", now=T0) is None


def test_delete_removes_the_row_and_is_safe_twice(db):
    s = _scholarship(db)
    _queue(db, s, 0)
    before = repo.size(db)
    assert repo.delete(db, s.id) is True
    assert repo.size(db) == before - 1
    assert repo.delete(db, s.id) is False


def test_size_counts_rows(db):
    base = repo.size(db)
    for i in range(3):
        _queue(db, _scholarship(db, str(i)), i)
    assert repo.size(db) == base + 3


def test_deleting_the_scholarship_removes_its_queue_row(db):
    s = _scholarship(db)
    _queue(db, s, 0)
    db.delete(s)
    db.flush()
    assert db.get(ClassifyPending, s.id) is None  # ON DELETE CASCADE (data-model.md)
