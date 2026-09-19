"""Minimal Application API (T107; contracts/openapi.yaml `POST /applications`).
Only the bare create endpoint ships here — `GET /applications` (tracker) and
the plan/assistant/submission-approval endpoints are Phase 4 (T126/T127) and
extend this same router file then, per Resolution note 3 in tasks.md.

`user_id` is always loaded server-side from the authenticated token, mirroring
`api/discovery.py`'s current-user pattern — never accepted in the request
body."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import application_repo, scholarship_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.document import ApplicationCreate, ApplicationRead

router = APIRouter()


@router.post("/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    body: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    scholarship = scholarship_repo.get_by_id(db, body.scholarship_id)
    if scholarship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scholarship not found")

    application = application_repo.create(db, current_user.id, body.scholarship_id)
    return ApplicationRead.model_validate(application)
