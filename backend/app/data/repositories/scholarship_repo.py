import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.scholarship import Scholarship, ScholarshipField
from app.models.source import ScholarshipSource


def get_by_id(db: Session, scholarship_id: uuid.UUID) -> Scholarship | None:
    stmt = (
        select(Scholarship)
        .where(Scholarship.id == scholarship_id)
        .options(selectinload(Scholarship.requirements), selectinload(Scholarship.funding_details))
    )
    return db.execute(stmt).scalar_one_or_none()


def get_by_official_url(db: Session, url: str) -> Scholarship | None:
    stmt = (
        select(Scholarship)
        .where(Scholarship.official_scholarship_url == url)
        .options(selectinload(Scholarship.requirements), selectinload(Scholarship.funding_details))
    )
    return db.execute(stmt).scalar_one_or_none()


def create(db: Session, scholarship: Scholarship) -> Scholarship:
    db.add(scholarship)
    db.commit()
    db.refresh(scholarship)
    return scholarship


def list_all(db: Session) -> list[Scholarship]:
    stmt = select(Scholarship).options(
        selectinload(Scholarship.requirements), selectinload(Scholarship.funding_details)
    )
    return list(db.execute(stmt).scalars().all())


def find_by_name(db: Session, name: str) -> list[Scholarship]:
    """Case/whitespace-normalized exact-name lookup — the DB-backed half of
    `dedup`'s deterministic (name+university+intake) key match (T087 fix):
    without this, two independent `run_ingestion` calls for the same
    scholarship (e.g. two discovery query-plan steps hitting different
    sources) would never see each other's already-persisted row."""
    normalized = name.strip().lower()
    if not normalized:
        return []
    stmt = select(Scholarship).where(func.lower(Scholarship.name) == normalized)
    return list(db.execute(stmt).scalars().all())


def get_field_rows(db: Session, scholarship_id: uuid.UUID) -> list[ScholarshipField]:
    stmt = select(ScholarshipField).where(ScholarshipField.scholarship_id == scholarship_id)
    return list(db.execute(stmt).scalars().all())


def list_for_source(db: Session, source_id: uuid.UUID) -> list[Scholarship]:
    """Every scholarship linked to `source_id` via `scholarship_sources`
    (002 monitoring scope: never the whole table). `distinct` because the
    ingestion merge path can add more than one provenance row per pair."""
    stmt = (
        select(Scholarship)
        .join(ScholarshipSource, ScholarshipSource.scholarship_id == Scholarship.id)
        .where(ScholarshipSource.source_id == source_id)
        .distinct()
    )
    return list(db.execute(stmt).scalars().all())


def mark_seen_verified(db: Session, scholarship_ids: Iterable[uuid.UUID], now: datetime) -> int:
    """002 D4: refresh `last_verified_at` to `now` for scholarships grounded-
    and-seen in a successful monitoring fetch. Touches nothing else."""
    ids = list(scholarship_ids)
    if not ids:
        return 0
    result = db.execute(
        update(Scholarship)
        .where(Scholarship.id.in_(ids))
        .values(last_verified_at=now)
        .execution_options(synchronize_session="fetch")
    )
    db.commit()
    return result.rowcount or 0
