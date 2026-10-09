"""Monitoring-run data access (002, data-model.md §4). System-owned rows, no
`user_id` scoping (same class as `source_registry`/`source_fetch_log`)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.models.monitoring import MonitoringRun, MonitoringStatus, MonitoringTrigger
from app.models.source import SourceFetchLog

# Constant key for the transaction-scoped advisory lock that serializes
# "is a run in progress? -> create one" (closes the read-then-write race).
_RUN_CREATE_LOCK_KEY = 0x4D4F4E52554E  # "MONRUN"


def create_run(db: Session, trigger: MonitoringTrigger) -> MonitoringRun:
    run = MonitoringRun(trigger=trigger, status=MonitoringStatus.RUNNING)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def has_running_run(db: Session) -> bool:
    stmt = select(MonitoringRun.id).where(MonitoringRun.status == MonitoringStatus.RUNNING).limit(1)
    return db.execute(stmt).first() is not None


def create_run_if_none_running(db: Session, trigger: MonitoringTrigger) -> MonitoringRun | None:
    """Atomic check-then-create: takes a TRANSACTION-scoped advisory lock on a
    constant key, checks for a running run, inserts if none, and commits (the
    commit releases the lock). A concurrent caller blocks on the lock until
    this transaction ends, then sees the new running row and gets `None`."""
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _RUN_CREATE_LOCK_KEY})
    if has_running_run(db):
        db.rollback()  # ends the transaction, releasing the lock
        return None
    return create_run(db, trigger)


def get_run(db: Session, run_id: uuid.UUID) -> MonitoringRun | None:
    return db.get(MonitoringRun, run_id)


def list_runs(db: Session, limit: int = 20) -> list[MonitoringRun]:
    stmt = select(MonitoringRun).order_by(MonitoringRun.started_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_fetch_logs_for_run(db: Session, run_id: uuid.UUID) -> list[SourceFetchLog]:
    stmt = (
        select(SourceFetchLog)
        .where(SourceFetchLog.monitoring_run_id == run_id)
        .order_by(SourceFetchLog.started_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def update_run_counters(
    db: Session, run_id: uuid.UUID, *, sources_processed: int, sources_failed: int, changes_detected: int
) -> None:
    """Progress write; a finished run (`ended_at` set) is never modified."""
    db.execute(
        update(MonitoringRun)
        .where(MonitoringRun.id == run_id, MonitoringRun.ended_at.is_(None))
        .values(
            sources_processed=sources_processed,
            sources_failed=sources_failed,
            changes_detected=changes_detected,
        )
    )
    db.commit()


def finish_run(
    db: Session,
    run_id: uuid.UUID,
    *,
    status: MonitoringStatus,
    sources_processed: int,
    sources_failed: int,
    changes_detected: int,
) -> bool:
    """Sets `ended_at` exactly once: the `WHERE ended_at IS NULL` guard makes a
    second call a no-op. Returns whether this call performed the finish."""
    result = db.execute(
        update(MonitoringRun)
        .where(MonitoringRun.id == run_id, MonitoringRun.ended_at.is_(None))
        .values(
            ended_at=datetime.now(timezone.utc),
            status=status,
            sources_processed=sources_processed,
            sources_failed=sources_failed,
            changes_detected=changes_detected,
        )
    )
    db.commit()
    return bool(result.rowcount)


def fail_leftover_running_runs(db: Session) -> int:
    """Process-crash recovery: any run still `running` at startup can never
    finish (its process is gone), so mark it failed and stamp `ended_at`."""
    result = db.execute(
        update(MonitoringRun)
        .where(MonitoringRun.status == MonitoringStatus.RUNNING, MonitoringRun.ended_at.is_(None))
        .values(status=MonitoringStatus.FAILED, ended_at=datetime.now(timezone.utc))
    )
    db.commit()
    return result.rowcount or 0
