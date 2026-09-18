"""Minimal application data access (Resolution note 3) — create/get only;
extended with plan/tracker/readiness queries in Phase 4 (T120)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application


def create(db: Session, user_id: uuid.UUID, scholarship_id: uuid.UUID) -> Application:
    application = Application(user_id=user_id, scholarship_id=scholarship_id)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def get_by_id_for_user(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> Application | None:
    stmt = select(Application).where(
        Application.id == application_id, Application.user_id == user_id
    )
    return db.execute(stmt).scalar_one_or_none()
