import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.scholarship import Scholarship


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
