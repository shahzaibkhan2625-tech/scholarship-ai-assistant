"""`source_registry` service (T078, Blueprint §14, §29) — a thin business-rule
layer over `data/repositories/source_repo.py`. Persistence and the
`status == active` governance gate already live in `source_repo` (Slice 2A);
this module does not duplicate that logic. It exists so other services
(dedup/verification/conflict_resolution/ingestion) have one reusable,
intention-revealing entry point for "is this source usable right now" and
registry CRUD orchestration, instead of importing `source_repo` internals
directly.
"""

import uuid

from sqlalchemy.orm import Session

from app.data.repositories import source_repo
from app.models.source import SourceRegistry
from app.schemas.source import SourceRegistryCreate

__all__ = [
    "list_active_sources",
    "is_source_active",
    "get_active_source",
    "register_source",
    "record_fetch_outcome",
]


def list_active_sources(
    db: Session, *, country: str | None = None, source_type: str | None = None
) -> list[SourceRegistry]:
    """Pass-through: `source_repo.get_active_sources` already gates on
    `status == active`."""
    return source_repo.get_active_sources(db, country=country, source_type=source_type)


def get_active_source(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    """Pass-through: `source_repo.get_source_by_id` already gates on
    `status == active`."""
    return source_repo.get_source_by_id(db, source_id)


def is_source_active(db: Session, source_id: uuid.UUID) -> bool:
    """Reusable active-only gating check for callers that only need a
    boolean (e.g. deciding whether a source's data can count as an
    "official-source confirmation" in the verification service)."""
    return get_active_source(db, source_id) is not None


def register_source(db: Session, data: SourceRegistryCreate) -> SourceRegistry:
    """Registry CRUD orchestration on top of `source_repo`'s idempotent
    upsert-by-domain primitive."""
    return source_repo.upsert_source_by_domain(db, data)


def record_fetch_outcome(db, log_data) -> None:
    """Pass-through to `source_repo.log_fetch`, kept here so pipeline
    services depend on `source_registry`, not `source_repo`, directly."""
    source_repo.log_fetch(db, log_data)
