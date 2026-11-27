"""SQL for per-source check state (003, data-model.md, research R-08).

Scoping: `source_id`-scoped system data, no `user_id`. Callers own the transaction.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.check_state import SourceCheckState
from app.models.source import SourceFetchLog

# Back-off is min(base * 2**level, cadence interval); a level this high is always at the interval.
MAX_BACKOFF_LEVEL = 16
_ZERO_CANDIDATES_PREFIX = "listing extraction yielded 0"
_UPDATABLE = {c.name for c in SourceCheckState.__table__.c} - {"source_id", "updated_at"}


def get(db: Session, source_id: uuid.UUID) -> SourceCheckState | None:
    return db.get(SourceCheckState, source_id)


def get_all(db: Session) -> dict[uuid.UUID, SourceCheckState]:
    """Every state row by source id (one query, for the due selection of a tick)."""
    return {state.source_id: state for state in db.execute(select(SourceCheckState)).scalars()}


def bootstrap_backoff_level(db: Session, source_id: uuid.UUID) -> int:
    """Count the most recent consecutive 'listing extraction yielded 0 candidates' fetch-log rows
    (newest first); the first other row ends the run. 0 when there is no such history."""
    stmt = (
        select(SourceFetchLog.error)
        .where(SourceFetchLog.source_id == source_id)
        .order_by(SourceFetchLog.started_at.desc(), SourceFetchLog.id.desc())
    )
    level = 0
    for error in db.execute(stmt).scalars():
        if error is None or not error.startswith(_ZERO_CANDIDATES_PREFIX):
            break
        level += 1
        if level >= MAX_BACKOFF_LEVEL:
            break
    return level


def get_or_create(db: Session, source_id: uuid.UUID) -> SourceCheckState:
    state = db.get(SourceCheckState, source_id)
    if state is None:
        state = SourceCheckState(
            source_id=source_id,
            backoff_level=bootstrap_backoff_level(db, source_id),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(state)
        db.flush()
    return state


def update(db: Session, source_id: uuid.UUID, **fields) -> SourceCheckState:
    unknown = set(fields) - _UPDATABLE
    if unknown:
        raise ValueError(f"unknown check-state field(s): {sorted(unknown)}")
    state = get_or_create(db, source_id)
    for name, value in fields.items():
        setattr(state, name, value)
    state.updated_at = datetime.now(timezone.utc)
    db.flush()
    return state
