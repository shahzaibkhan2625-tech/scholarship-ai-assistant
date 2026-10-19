"""Coverage route (002 US4, FR-COVAN-1..4, contracts `/coverage`).

Bearer auth like `/sources` and `/sources/health` (no operator gate yet). Read-only;
all aggregation lives in `services/coverage.py`, computed live per request."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.coverage import CoverageSnapshot
from app.services.coverage import get_coverage_summary

router = APIRouter()


@router.get("/coverage", response_model=CoverageSnapshot)
def get_coverage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverageSnapshot:
    return CoverageSnapshot.model_validate(get_coverage_summary(db).model_dump())
