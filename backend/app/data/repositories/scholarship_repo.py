import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.scholarship import Scholarship, ScholarshipField


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
