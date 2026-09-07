"""Source Registry data access (Blueprint §14, §29, §33).

Governance is enforced here, not by prompt instruction (constitution
Principle IV): `get_active_sources`/`get_source_by_id` only ever return rows
with `status == active`, and a `candidate_sources` row is promoted into
`source_registry` — a distinct table — only via `approve_candidate_source`.
`SourceNotApprovedError` is exposed for the connector layer (WS2.2) to raise
when a fetch is attempted against a non-active `source_id`.
"""

import uuid
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import CandidateSource, CandidateSourceStatus, SourceFetchLog, SourceRegistry, SourceStatus
from app.schemas.source import CandidateSourceCreate, SourceFetchLogCreate, SourceRegistryCreate


class SourceNotApprovedError(Exception):
    """Raised when a fetch is attempted against a source_id that is not an
    active, approved `source_registry` row."""


class CandidateSourceNotFoundError(Exception):
    """Raised when a candidate_sources id does not exist."""


class CandidateSourceAlreadyReviewedError(Exception):
    """Raised when approve/reject is attempted on a non-pending candidate."""


def get_active_sources(
    db: Session, country: str | None = None, source_type: str | None = None
) -> list[SourceRegistry]:
    stmt = select(SourceRegistry).where(SourceRegistry.status == SourceStatus.ACTIVE)
    if country is not None:
        stmt = stmt.where(SourceRegistry.country == country)
    if source_type is not None:
        stmt = stmt.where(SourceRegistry.source_type == source_type)
    return list(db.execute(stmt).scalars().all())


def get_source_by_id(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    stmt = select(SourceRegistry).where(
        SourceRegistry.id == source_id, SourceRegistry.status == SourceStatus.ACTIVE
    )
    return db.execute(stmt).scalar_one_or_none()


def get_source_by_domain(db: Session, domain: str) -> SourceRegistry | None:
    stmt = select(SourceRegistry).where(SourceRegistry.domain == domain)
    return db.execute(stmt).scalar_one_or_none()


def upsert_source_by_domain(db: Session, data: SourceRegistryCreate) -> SourceRegistry:
    """Idempotent seed-loader primitive: update the row for `data.domain` if
    one exists, otherwise insert. Never deletes."""

    existing = get_source_by_domain(db, data.domain)
    if existing is not None:
        for field, value in data.model_dump().items():
            setattr(existing, field, value)
        db.commit()
        db.refresh(existing)
        return existing

    source = SourceRegistry(**data.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def create_candidate_source(db: Session, data: CandidateSourceCreate) -> CandidateSource:
    candidate = CandidateSource(**data.model_dump(), status=CandidateSourceStatus.PENDING)
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def get_candidate_source_by_id(db: Session, candidate_id: uuid.UUID) -> CandidateSource | None:
    return db.get(CandidateSource, candidate_id)


def list_candidate_sources(
    db: Session, status: CandidateSourceStatus | None = CandidateSourceStatus.PENDING
) -> list[CandidateSource]:
    stmt = select(CandidateSource)
    if status is not None:
        stmt = stmt.where(CandidateSource.status == status)
    return list(db.execute(stmt).scalars().all())


def approve_candidate_source(
    db: Session,
    candidate_id: uuid.UUID,
    reviewed_by: uuid.UUID,
    source_data: SourceRegistryCreate,
) -> SourceRegistry:
    """Promotes a pending candidate into `source_registry` with
    `status = active`. `source_data` is the reviewer-confirmed registry entry
    (name, reliability_level, extraction_rules, etc.) — a `candidate_sources`
    row alone (url + signals) does not carry enough verified information to
    synthesize an authoritative registry entry without guessing (constitution
    Principle I: never invent facts)."""

    candidate = get_candidate_source_by_id(db, candidate_id)
    if candidate is None:
        raise CandidateSourceNotFoundError(f"candidate_source {candidate_id} not found")
    if candidate.status != CandidateSourceStatus.PENDING:
        raise CandidateSourceAlreadyReviewedError(
            f"candidate_source {candidate_id} already {candidate.status}"
        )

    payload = source_data.model_dump()
    payload["status"] = SourceStatus.ACTIVE
    source = SourceRegistry(**payload)
    db.add(source)

    candidate.status = CandidateSourceStatus.APPROVED
    candidate.reviewed_by = reviewed_by
    candidate.reviewed_at = datetime.now()

    db.commit()
    db.refresh(source)
    return source


def reject_candidate_source(
    db: Session, candidate_id: uuid.UUID, reviewed_by: uuid.UUID, reason: str
) -> CandidateSource:
    candidate = get_candidate_source_by_id(db, candidate_id)
    if candidate is None:
        raise CandidateSourceNotFoundError(f"candidate_source {candidate_id} not found")
    if candidate.status != CandidateSourceStatus.PENDING:
        raise CandidateSourceAlreadyReviewedError(
            f"candidate_source {candidate_id} already {candidate.status}"
        )

    candidate.status = CandidateSourceStatus.REJECTED
    candidate.reviewed_by = reviewed_by
    candidate.reviewed_at = datetime.now()
    candidate.signals = {**candidate.signals, "rejection_reason": reason}

    db.commit()
    db.refresh(candidate)
    return candidate


def log_fetch(db: Session, data: SourceFetchLogCreate) -> SourceFetchLog:
    payload = data.model_dump(exclude_unset=False)
    if payload.get("started_at") is None:
        payload.pop("started_at")
    log = SourceFetchLog(**payload)
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_fetch_logs_for_source(db: Session, source_id: uuid.UUID) -> list[SourceFetchLog]:
    stmt = (
        select(SourceFetchLog)
        .where(SourceFetchLog.source_id == source_id)
        .order_by(SourceFetchLog.started_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def domain_from_url(url: str) -> str:
    """Best-effort bare-hostname extraction for pre-filling a candidate's
    proposed domain — never used to fabricate a registry entry outright."""

    return urlparse(url).netloc
