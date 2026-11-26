"""SQL for the pending-classification queue (003 FR-BUDGET-13, data-model.md).

Scoping: `scholarship_id`-keyed system data, no `user_id`. Callers own the transaction (flush only, never
commit), so ingestion can write a record and its queue row together. `last_reason` is a reason class, never
provider text.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.classify_pending import ClassifyPending


def enqueue(db: Session, scholarship_id: uuid.UUID, reason: str) -> ClassifyPending:
    """One row per record: queueing a record that is already queued only refreshes `last_reason`, so the
    oldest-first order is never reset."""
    row = db.get(ClassifyPending, scholarship_id)
    if row is None:
        row = ClassifyPending(scholarship_id=scholarship_id, queued_at=datetime.now(timezone.utc), attempts=0)
        db.add(row)
    row.last_reason = reason
    db.flush()
    return row


def list_oldest(db: Session, limit: int) -> list[ClassifyPending]:
    """Oldest first; the record id breaks a tie in queue time so the order is deterministic."""
    stmt = select(ClassifyPending).order_by(ClassifyPending.queued_at, ClassifyPending.scholarship_id).limit(limit)
    return list(db.execute(stmt).scalars().all())


def record_attempt(db: Session, scholarship_id: uuid.UUID, reason: str, *, now: datetime) -> ClassifyPending | None:
    row = db.get(ClassifyPending, scholarship_id)
    if row is None:
        return None
    row.attempts += 1
    row.last_reason = reason
    row.last_attempt_at = now
    db.flush()
    return row


def delete(db: Session, scholarship_id: uuid.UUID) -> bool:
    row = db.get(ClassifyPending, scholarship_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def size(db: Session) -> int:
    return db.execute(select(func.count()).select_from(ClassifyPending)).scalar_one()
