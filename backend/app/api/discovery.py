"""Discovery API (T090, Blueprint §7.1 / §22 Phase 2 DoD). Wires the existing
Discovery Agent (T089, `run_discovery`) behind a JWT-protected `POST
/discovery` — pure exposure, no new business logic. The caller's profile is
loaded server-side from the authenticated user (matches how Phase 1
endpoints resolve the current user's data, e.g. `GET /profile`), never
accepted in the request body.

Discovery is profile-driven (`build_query_plan` in the agent plans off
nationality/target_degree_level/target_fields/target_countries), so a
profile with none of those set yet cannot produce a meaningful plan; that
case is rejected with a clear 4xx rather than silently returning an empty
result set."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.discovery.agent import run_discovery
from app.api.deps import get_current_user
from app.data.repositories.db import get_db
from app.models.profile import Profile
from app.models.user import User
from app.schemas.discovery import DiscoveryResult
from app.services.profile import get_or_create_profile

router = APIRouter()


def _has_discovery_criteria(profile: Profile) -> bool:
    return bool(
        profile.nationality
        or profile.target_degree_level
        or profile.target_fields
        or profile.target_countries
    )


@router.post("", response_model=DiscoveryResult)
def discover(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiscoveryResult:
    profile = get_or_create_profile(db, current_user.id)
    if not _has_discovery_criteria(profile):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Profile setup is required before discovery can run. Set at least one of "
                "nationality, target degree level, target fields, or target countries first."
            ),
        )

    run = run_discovery(db, profile)
    return run.discovery_result
