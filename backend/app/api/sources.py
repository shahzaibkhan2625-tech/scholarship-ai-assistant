"""Source Registry governance routes (Blueprint §29).

There is deliberately no `POST /sources`: an approved `source_registry` row
enters only via the seed file (`app/services/source_registry_seed.py`) or
by approving a `candidate_sources` row through this router — never through
a direct-create endpoint (constitution Principle IV, table-separation
governance)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_operator
from app.data.repositories import candidate_validation_repo, source_repo
from app.data.repositories.db import get_db
from app.core.url_safety import _resolve_host, is_public_hostname
from app.data.repositories.source_repo import (
    CandidateSourceAlreadyReviewedError,
    CandidateSourceNotFoundError,
    DuplicateSourceDomainError,
)
from app.models.source import CandidateSourceStatus
from app.models.user import User
from app.schemas.candidate_validation import CandidateSourceValidationRead
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


@router.post(
    "/sources/candidates/{candidate_id}/approve",
    response_model=SourceRegistryRead,
    responses={
        403: {"description": "operator access required"},
        404: {"description": "candidate_id not found"},
        409: {"description": "candidate already approved/rejected, or the source domain is already registered"},
        422: {"description": "domain is not a public host name"},
    },
)
def approve_candidate(
    candidate_id: uuid.UUID,
    source_data: SourceRegistryCreate,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> SourceRegistryRead:
    # 010 FR-SSRF-6: the DNS half of the public-host rule (the static half ran in the schema).
    reason = is_public_hostname(source_data.domain, resolve=_resolve_host)
    if reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"domain must be a public host name ({reason})"
        )
    try:
        source = source_repo.approve_candidate_source(db, candidate_id, current_user.id, source_data)
    except CandidateSourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CandidateSourceAlreadyReviewedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DuplicateSourceDomainError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source domain already registered") from exc
    return SourceRegistryRead.model_validate(source)


@router.post(
    "/sources/candidates/{candidate_id}/reject",
    response_model=CandidateSourceRead,
    responses={
        403: {"description": "operator access required"},
        404: {"description": "candidate_id not found"},
        409: {"description": "candidate already approved/rejected"},
    },
)
def reject_candidate(
    candidate_id: uuid.UUID,
    body: CandidateSourceReject,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> CandidateSourceRead:
    try:
        candidate = source_repo.reject_candidate_source(db, candidate_id, current_user.id, body.reason)
    except CandidateSourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CandidateSourceAlreadyReviewedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return CandidateSourceRead.model_validate(candidate)


@router.get(
    "/sources/candidates/{candidate_id}/validations",
    response_model=list[CandidateSourceValidationRead],
    responses={
        403: {"description": "operator access required"},
        404: {"description": "candidate_id not found"},
    },
)
def list_candidate_validations(
    candidate_id: uuid.UUID,
    current_user: User = Depends(require_operator),
    db: Session = Depends(get_db),
) -> list[CandidateSourceValidationRead]:
    """Advisory pre-check history, newest first (US7). Read-only: it informs the
    approve/reject decision and never gates it."""
    if source_repo.get_candidate_source_by_id(db, candidate_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"candidate_source {candidate_id} not found")
    rows = candidate_validation_repo.list_for_candidate(db, candidate_id)
    return [CandidateSourceValidationRead.model_validate(row) for row in rows]
