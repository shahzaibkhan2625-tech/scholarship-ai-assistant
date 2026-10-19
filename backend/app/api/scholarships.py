"""Scholarship lifecycle read route (002 US5, FR-LIFECYCLE-1, contracts `/scholarships/{scholarship_id}`).

Bearer auth like `/sources/health` and `/coverage` (no operator gate yet). Read-only:
the only way a record's lifecycle changes is a monitoring/ingestion outcome."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import scholarship_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.scholarship import ScholarshipLifecycleView

router = APIRouter()


@router.get(
    "/scholarships/{scholarship_id}",
    response_model=ScholarshipLifecycleView,
    responses={404: {"description": "scholarship_id not found"}},
)
def get_scholarship(
    scholarship_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScholarshipLifecycleView:
    scholarship = scholarship_repo.get_by_id(db, scholarship_id)
    if scholarship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scholarship not found")
    return ScholarshipLifecycleView.model_validate(scholarship)
