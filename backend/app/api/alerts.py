"""Alert routes (002 FR-ALERT-4/5). Every route is scoped to the authenticated
user; another user's alert is indistinguishable from a missing one (404)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import alert_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.alert import AlertPreferencesRead, AlertPreferencesUpdate, AlertRead

router = APIRouter()


@router.get("/alerts", response_model=list[AlertRead])
def list_alerts(
    unread_only: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AlertRead]:
    rows = alert_repo.list_for_user(db, current_user.id, unread_only=unread_only)
    return [AlertRead.model_validate(row) for row in rows]


@router.get("/alerts/preferences", response_model=AlertPreferencesRead)
def get_alert_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlertPreferencesRead:
    return AlertPreferencesRead.model_validate(alert_repo.get_preferences(db, current_user.id))


@router.put("/alerts/preferences", response_model=AlertPreferencesRead)
def update_alert_preferences(
    body: AlertPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlertPreferencesRead:
    row = alert_repo.upsert_preferences(
        db, current_user.id, in_app_enabled=body.in_app_enabled, email_enabled=body.email_enabled
    )
    return AlertPreferencesRead.model_validate(row)


@router.post(
    "/alerts/{alert_id}/read",
    response_model=AlertRead,
    responses={404: {"description": "alert does not exist or belongs to another user"}},
)
def mark_alert_read(
    alert_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlertRead:
    alert = alert_repo.mark_read(db, current_user.id, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return AlertRead.model_validate(alert)
