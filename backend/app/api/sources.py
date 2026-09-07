"""Source Registry governance routes (Blueprint §29).

There is deliberately no `POST /sources`: an approved `source_registry` row
enters only via the seed file (`app/services/source_registry_seed.py`) or
by approving a `candidate_sources` row through this router — never through
a direct-create endpoint (constitution Principle IV, table-separation
governance)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import source_repo
from app.data.repositories.db import get_db
from app.data.repositories.source_repo import CandidateSourceAlreadyReviewedError, CandidateSourceNotFoundError
from app.models.source import CandidateSourceStatus
from app.models.user import User
from app.schemas.source import (
    CandidateSourceReject,
    CandidateSourceRead,
    SourceRegistryCreate,
    SourceRegistryRead,
)

router = APIRouter()


@router.get("/sources", response_model=list[SourceRegistryRead])
def list_active_sources(
    country: str | None = None,
    source_type: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SourceRegistryRead]:
    sources = source_repo.get_active_sources(db, country=country, source_type=source_type)
    return [SourceRegistryRead.model_validate(source) for source in sources]


@router.get("/sources/candidates", response_model=list[CandidateSourceRead])
def list_pending_candidates(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CandidateSourceRead]:
    candidates = source_repo.list_candidate_sources(db, status=CandidateSourceStatus.PENDING)
    return [CandidateSourceRead.model_validate(candidate) for candidate in candidates]


@router.post("/sources/candidates/{candidate_id}/approve", response_model=SourceRegistryRead)
def approve_candidate(
    candidate_id: uuid.UUID,
    source_data: SourceRegistryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SourceRegistryRead:
    try:
        source = source_repo.approve_candidate_source(db, candidate_id, current_user.id, source_data)
    except CandidateSourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CandidateSourceAlreadyReviewedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return SourceRegistryRead.model_validate(source)


@router.post("/sources/candidates/{candidate_id}/reject", response_model=CandidateSourceRead)
def reject_candidate(
    candidate_id: uuid.UUID,
    body: CandidateSourceReject,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CandidateSourceRead:
    try:
        candidate = source_repo.reject_candidate_source(db, candidate_id, current_user.id, body.reason)
    except CandidateSourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CandidateSourceAlreadyReviewedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CandidateSourceRead.model_validate(candidate)
