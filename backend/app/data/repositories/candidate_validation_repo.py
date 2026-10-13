"""Append-only repository for `candidate_source_validations` (002 US7).

Rows are only ever inserted, never updated or deleted; "current" is the latest
row by `checked_at`. `checked_at` is set Python-side (injectable) rather than
left to the column's `server_default now()`, because `now()` is the TRANSACTION
start time: two validations written in one transaction would tie and "latest"
would be arbitrary. `id` is a secondary key only so the order is stable, not
because it encodes insertion order (it is a random UUID).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_validation import CandidateSourceValidation

__all__ = ["add", "list_for_candidate", "latest_for_candidate"]


def add(
    db: Session,
    candidate_source_id: uuid.UUID,
    *,
    reachable: bool,
    extractable: bool,
    official_signals: dict,
    reachable_detail: str | None = None,
    extractable_detail: str | None = None,
    checked_at: datetime | None = None,
) -> CandidateSourceValidation:
    row = CandidateSourceValidation(
        candidate_source_id=candidate_source_id,
        checked_at=checked_at or datetime.now(timezone.utc),
        reachable=reachable,
        reachable_detail=reachable_detail,
        extractable=extractable,
        extractable_detail=extractable_detail,
        official_signals=official_signals,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_for_candidate(db: Session, candidate_source_id: uuid.UUID) -> list[CandidateSourceValidation]:
    stmt = (
        select(CandidateSourceValidation)
        .where(CandidateSourceValidation.candidate_source_id == candidate_source_id)
        .order_by(CandidateSourceValidation.checked_at.desc(), CandidateSourceValidation.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def latest_for_candidate(db: Session, candidate_source_id: uuid.UUID) -> CandidateSourceValidation | None:
    stmt = (
        select(CandidateSourceValidation)
        .where(CandidateSourceValidation.candidate_source_id == candidate_source_id)
        .order_by(CandidateSourceValidation.checked_at.desc(), CandidateSourceValidation.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()
