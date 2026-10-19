"""Source-health routes (002 US3, FR-HEALTH-1..4, FR-FETCH-3).

Bearer auth like `/sources` (no operator gate yet). Read-only. Must be included
BEFORE the sources router in `main.py` so `/sources/health` is never captured by
a `/sources/{...}` path parameter."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import source_repo
from app.data.repositories.db import get_db
from app.models.source import SourceStatus
from app.models.user import User
from app.schemas.health import HealthStatus, SourceFetchLogEntry, SourceHealth

router = APIRouter()


@router.get("/sources/health", response_model=list[SourceHealth])
def get_sources_health(
    status_filter: HealthStatus | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SourceHealth]:
    rows = source_repo.list_source_health(db, SourceStatus(status_filter) if status_filter else None)
    return [
        SourceHealth(
            source_id=source.id,
            name=source.name,
            status=source.status.value,
            last_checked_at=source.last_checked_at,
            last_success_at=source.last_success_at,
            consecutive_failures=failures,
            listing_page_url=source.listing_page_url,
        )
        for source, failures in rows
    ]


@router.get(
    "/sources/{source_id}/fetch-log",
    response_model=list[SourceFetchLogEntry],
    responses={404: {"description": "source_id not found"}},
)
def get_source_fetch_log(
    source_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SourceFetchLogEntry]:
    if source_repo.get_source_any_status(db, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return [
        SourceFetchLogEntry.model_validate(log) for log in source_repo.get_recent_fetch_logs(db, source_id, limit)
    ]
