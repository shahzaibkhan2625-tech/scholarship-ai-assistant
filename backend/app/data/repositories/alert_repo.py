"""Alert data access (002, data-model.md §5–6). Every user-facing read/write is
scoped by `user_id`; the owner-selection helpers at the bottom are the only
cross-user queries and are used only by the monitoring-side alert service."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alert import Alert, AlertPreferences
from app.models.application import Application
from app.models.match import Match
from app.models.profile import Profile


def create(db: Session, alert: Alert) -> Alert:
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def save(db: Session, alert: Alert) -> Alert:
    """Persist delivery-status changes on the SAME row (no duplicate on retry)."""
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def list_for_user(db: Session, user_id: uuid.UUID, *, unread_only: bool = False) -> list[Alert]:
    stmt = select(Alert).where(Alert.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Alert.read_at.is_(None))
    return list(db.execute(stmt.order_by(Alert.created_at.desc())).scalars().all())


def get_for_user(db: Session, user_id: uuid.UUID, alert_id: uuid.UUID) -> Alert | None:
    stmt = select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def mark_read(db: Session, user_id: uuid.UUID, alert_id: uuid.UUID) -> Alert | None:
    """Sets `read_at` once; a repeat call keeps the original timestamp."""
    alert = get_for_user(db, user_id, alert_id)
    if alert is None:
        return None
    if alert.read_at is None:
        alert.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(alert)
    return alert


def exists_for_run(db: Session, user_id: uuid.UUID, scholarship_id: uuid.UUID, monitoring_run_id: uuid.UUID) -> bool:
    stmt = (
        select(Alert.id)
        .where(
            Alert.user_id == user_id,
            Alert.scholarship_id == scholarship_id,
            Alert.monitoring_run_id == monitoring_run_id,
        )
        .limit(1)
    )
    return db.execute(stmt).first() is not None


def get_preferences(db: Session, user_id: uuid.UUID) -> AlertPreferences:
    """The stored row, or an unsaved defaults object (both channels on)."""
    row = db.get(AlertPreferences, user_id)
    if row is not None:
        return row
    return AlertPreferences(user_id=user_id, in_app_enabled=True, email_enabled=True)


def upsert_preferences(
    db: Session, user_id: uuid.UUID, *, in_app_enabled: bool | None = None, email_enabled: bool | None = None
) -> AlertPreferences:
    row = db.get(AlertPreferences, user_id)
    if row is None:
        row = AlertPreferences(user_id=user_id, in_app_enabled=True, email_enabled=True)
        db.add(row)
    if in_app_enabled is not None:
        row.in_app_enabled = in_app_enabled
    if email_enabled is not None:
        row.email_enabled = email_enabled
    db.commit()
    db.refresh(row)
    return row


# --- owner selection (cross-user, monitoring side only) -----------------------------


def profile_user_ids(db: Session) -> list[uuid.UUID]:
    """Every owner who has a profile (reads only; never creates one)."""
    return list(db.execute(select(Profile.user_id)).scalars().all())


def user_ids_with_match_or_application(db: Session, scholarship_id: uuid.UUID) -> list[uuid.UUID]:
    matched = select(Match.user_id).where(Match.scholarship_id == scholarship_id)
    applied = select(Application.user_id).where(Application.scholarship_id == scholarship_id)
    ids = set(db.execute(matched).scalars().all()) | set(db.execute(applied).scalars().all())
    return sorted(ids, key=str)
