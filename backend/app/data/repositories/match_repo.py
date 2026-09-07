"""Match repository — immutable, append-only writes. A `matches` row is
never updated; re-matching always produces a new row (data-model.md §4
State transitions: history preserved for the tracker)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.match import Match


def create(db: Session, match: Match) -> Match:
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


def list_for_user(db: Session, user_id: uuid.UUID) -> list[Match]:
    stmt = select(Match).where(Match.user_id == user_id).order_by(Match.created_at.desc())
    return list(db.execute(stmt).scalars().all())


def latest_for_user_and_scholarship(db: Session, user_id: uuid.UUID, scholarship_id: uuid.UUID) -> Match | None:
    stmt = (
        select(Match)
        .where(Match.user_id == user_id, Match.scholarship_id == scholarship_id)
        .order_by(Match.created_at.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
