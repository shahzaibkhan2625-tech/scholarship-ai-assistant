"""010 T333: audit_repo.record adds a row in the caller's transaction and never commits."""

import uuid

import pytest
from sqlalchemy import func, select

from app.data.repositories import audit_repo
from app.models.audit import AuditLog


def test_closed_action_set():
    assert audit_repo.AUDIT_ACTIONS == frozenset(
        {"user.signup", "source.approve", "source.reject", "monitoring.run.manual", "submission.approve", "operator.grant"}
    )


@pytest.mark.parametrize("action", sorted(audit_repo.AUDIT_ACTIONS))
def test_record_stores_the_row(db_session_factory, action):
    user_id = uuid.uuid4()
    entity = f"user:{uuid.uuid4()}"
    with db_session_factory() as db:
        audit_repo.record(db, user_id, action, entity)
        db.flush()
        row = db.execute(select(AuditLog).where(AuditLog.user_id == user_id)).scalar_one()
        assert (row.action, row.entity) == (action, entity)
        assert row.at is not None


def test_record_never_commits_so_a_rollback_removes_it(db_session_factory):
    user_id = uuid.uuid4()
    with db_session_factory() as db:
        audit_repo.record(db, user_id, "user.signup", f"user:{user_id}")
        db.rollback()
        count = db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.user_id == user_id)).scalar_one()
        assert count == 0


def test_record_only_adds_to_the_session(db_session_factory):
    user_id = uuid.uuid4()
    with db_session_factory() as db:
        audit_repo.record(db, user_id, "user.signup", f"user:{user_id}")
        assert len(db.new) == 1


def test_unknown_action_is_rejected_and_nothing_is_added(db_session_factory):
    with db_session_factory() as db:
        with pytest.raises(ValueError):
            audit_repo.record(db, uuid.uuid4(), "user.delete", f"user:{uuid.uuid4()}")
        assert not db.new
