import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import match_repo, scholarship_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.match import MatchVerdict
from app.services.matching import match_scholarship
from app.services.profile import get_or_create_profile
from app.services.ranking import rank_matches

router = APIRouter()


@router.post("/scholarships/{scholarship_id}/match", response_model=MatchVerdict)
def match_scholarship_by_id(
    scholarship_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchVerdict:
    scholarship = scholarship_repo.get_by_id(db, scholarship_id)
    if scholarship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scholarship not found")

    profile = get_or_create_profile(db, current_user.id)
    return match_scholarship(db, current_user.id, profile, scholarship)


@router.get("/matches", response_model=list[MatchVerdict])
def list_matches(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MatchVerdict]:
    rows = match_repo.list_for_user(db, current_user.id)
    verdicts = [
        MatchVerdict(
            scholarship_id=row.scholarship_id,
            eligibility_verdict=row.eligibility_verdict,
            match_strength=row.match_strength,
            hard_constraints=row.hard_constraints,
            soft_preferences=row.soft_preferences,
            exclusions_triggered=row.exclusions_triggered,
            matched_criteria=row.matched_criteria,
            failed_criteria=row.failed_criteria,
            missing_information=row.missing_information,
            unverified_criteria=row.unverified_criteria,
            required_documents=row.required_documents,
            remaining_actions=row.remaining_actions,
            evidence=row.evidence,
        )
        for row in rows
    ]
    return rank_matches(verdicts)
