"""Source Registry data access (Blueprint §14, §29, §33).

Governance is enforced here, not by prompt instruction (constitution
Principle IV): `get_active_sources`/`get_source_by_id` only ever return rows
with `status == active`, and a `candidate_sources` row is promoted into
`source_registry` — a distinct table — only via `approve_candidate_source`.
`SourceNotApprovedError` is exposed for the connector layer (WS2.2) to raise
when a fetch is attempted against a non-active `source_id`.
"""

import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source import (
    CandidateSource,
    CandidateSourceStatus,
    ScholarshipSource,
    SourceFetchLog,
    SourceRegistry,
    SourceStatus,
    SourceType,
)
from app.schemas.source import CandidateSourceCreate, SourceFetchLogCreate, SourceRegistryCreate


_PRESERVE_IF_OMITTED = frozenset({"listing_page_url", "freshness_window_days"})


class SourceNotApprovedError(Exception):
    """Raised when a fetch is attempted against a source_id that is not an
    active, approved `source_registry` row."""


class CandidateSourceNotFoundError(Exception):
    """Raised when a candidate_sources id does not exist."""


class CandidateSourceAlreadyReviewedError(Exception):
    """Raised when approve/reject is attempted on a non-pending candidate."""


def get_active_sources(
    db: Session,
    country: str | None = None,
    source_type: str | None = None,
    *,
    discovery_role: bool | None = None,
    access_method: str | None = None,
) -> list[SourceRegistry]:
    """`discovery_role`/`access_method` (002 T178) are keyword-only and default
    `None` = no filter, so every existing caller is unchanged."""
    stmt = select(SourceRegistry).where(SourceRegistry.status == SourceStatus.ACTIVE)
    if country is not None:
        stmt = stmt.where(SourceRegistry.country == country)
    if source_type is not None:
        stmt = stmt.where(SourceRegistry.source_type == source_type)
    if discovery_role is not None:
        stmt = stmt.where(SourceRegistry.discovery_role == discovery_role)
    if access_method is not None:
        stmt = stmt.where(SourceRegistry.access_method == access_method)
    return list(db.execute(stmt).scalars().all())


def get_all_sources(db: Session) -> list[SourceRegistry]:
    """Every configured source regardless of status — for admin/coverage
    reporting (T084) only. Never used to gate a fetch; `get_active_sources`/
    `get_source_by_id` remain the sole authoritative-fetch gate."""
    return list(db.execute(select(SourceRegistry)).scalars().all())


def get_all_fetch_logs(db: Session) -> list[SourceFetchLog]:
    """Every fetch-log row across all sources — for coverage aggregation
    (T084) only, so it doesn't need to re-query per source."""
    return list(db.execute(select(SourceFetchLog)).scalars().all())


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
    one exists, otherwise insert. Never deletes.

    Every 001 field is full-replace on update (unchanged). The 002 fields in
    `_PRESERVE_IF_OMITTED` follow `model_fields_set` instead: omitted keeps the
    stored value, an explicit `None` clears it — so a pre-002 seed that doesn't
    know about them can't silently wipe an operator-set `listing_page_url`."""

    existing = get_source_by_domain(db, data.domain)
    if existing is not None:
        for field, value in data.model_dump().items():
            if field in _PRESERVE_IF_OMITTED and field not in data.model_fields_set:
                continue
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


def mark_source_failing(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    """Flips a source to `failing` after retries are exhausted (§33). Looks up
    by id directly (not `get_source_by_id`, which filters to `active` only)
    since a source that is already `failing`/`disabled` is still a valid
    target for this — it only ever tightens status, never fabricates a row."""

    source = db.get(SourceRegistry, source_id)
    if source is None:
        return None
    source.status = SourceStatus.FAILING
    db.commit()
    db.refresh(source)
    return source


def touch_source_check(
    db: Session, source_id: uuid.UUID, *, success: bool, now: datetime | None = None
) -> SourceRegistry | None:
    """002 monitoring bookkeeping: `last_checked_at` is set on every check,
    `last_success_at` only when `success`. Never changes `status`. Looks up by
    id directly (not active-only) like `mark_source_failing`."""
    source = db.get(SourceRegistry, source_id)
    if source is None:
        return None
    stamp = now or datetime.now(timezone.utc)
    source.last_checked_at = stamp
    if success:
        source.last_success_at = stamp
    db.commit()
    db.refresh(source)
    return source


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
    """Scoping (T132 standing rule): `source_id`-scoped, not `user_id`-scoped
    — `source_fetch_log` rows belong to the global governed `source_registry`,
    never to an individual user, so no `user_id` filter applies here."""
    stmt = (
        select(SourceFetchLog)
        .where(SourceFetchLog.source_id == source_id)
        .order_by(SourceFetchLog.started_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_scholarship_sources_by_source(db: Session, source_id: uuid.UUID) -> list[ScholarshipSource]:
    """Scoping (T132 standing rule): `source_id`-scoped, not `user_id`-scoped
    — `scholarship_sources` rows are global governed provenance data (which
    registry source a scholarship was retrieved from), never user-owned, so
    no `user_id` filter applies here. Read-only; used by the Discovery
    Agent's cross-run extraction cache (T135/A9) to rebuild a prior run's
    results for a source that is still within its staleness window, without
    re-fetching or re-extracting it."""
    stmt = select(ScholarshipSource).where(ScholarshipSource.source_id == source_id)
    return list(db.execute(stmt).scalars().all())


def domain_from_url(url: str) -> str:
    """Best-effort bare-hostname extraction for pre-filling a candidate's
    proposed domain — never used to fabricate a registry entry outright."""

    return urlparse(url).netloc


def record_or_bump_candidate_source(
    db: Session,
    *,
    domain: str,
    url: str,
    discovered_from: str | None,
    proposed_type: SourceType | None = None,
    matched_keyword: str | None = None,
) -> CandidateSource | None:
    """US4 Acceptance Scenario 3 / ADR-0005: the single write entry point for
    a domain surfaced by `app.tools.detect_candidate_links` during a listing
    fetch. Always inserts with `status == pending` (enforced by
    `create_candidate_source` itself, never overridable here) — never
    promotes, never reads `candidate_sources` back for use as an
    authoritative source.

    Dedup (ADR-0005 Decision 4), in order:
    1. `domain` already resolves to a `source_registry` row in ANY status
       (active/pending/disabled/failing) -> no-op, returns `None`. A domain
       the registry already knows about is never re-proposed as "new."
    2. An existing `candidate_sources` row for this `domain` that is NOT
       `pending` (already `approved` or `rejected`) -> no-op, returns `None`.
       A human already decided this domain; this function never resurrects
       that decision.
    3. An existing `pending` row for this `domain` -> bumped in place
       (`signals["seen_count"]` incremented, `last_seen_at` refreshed,
       `discovered_from` URL added if new) — never a second row for the
       same domain.
    4. Otherwise -> a new `pending` row is created via `create_candidate_source`.

    The domain-match scan (`list_candidate_sources(db, status=None)` +
    `domain_from_url` comparison) is O(n) in the total `candidate_sources`
    row count — an explicit, accepted MVP-scale assumption (ADR-0005
    Consequences/Negative): candidates are rare, human-reviewed events, not
    a high-volume table."""

    if get_source_by_domain(db, domain) is not None:
        return None

    now = datetime.now().isoformat()
    existing = [
        candidate
        for candidate in list_candidate_sources(db, status=None)
        if domain_from_url(candidate.url) == domain
    ]

    pending_match: CandidateSource | None = None
    for candidate in existing:
        if candidate.status != CandidateSourceStatus.PENDING:
            return None
        if pending_match is None:
            pending_match = candidate

    if pending_match is not None:
        signals = dict(pending_match.signals)
        signals["seen_count"] = signals.get("seen_count", 1) + 1
        signals["last_seen_at"] = now
        if matched_keyword:
            signals["matched_keyword"] = matched_keyword
        if discovered_from:
            discovered_from_urls = list(signals.get("discovered_from_urls", []))
            if discovered_from not in discovered_from_urls:
                discovered_from_urls.append(discovered_from)
            signals["discovered_from_urls"] = discovered_from_urls
        pending_match.signals = signals
        db.commit()
        db.refresh(pending_match)
        return pending_match

    new_signals: dict = {"seen_count": 1, "first_seen_at": now, "last_seen_at": now}
    if matched_keyword:
        new_signals["matched_keyword"] = matched_keyword
    if discovered_from:
        new_signals["discovered_from_urls"] = [discovered_from]

    return create_candidate_source(
        db,
        CandidateSourceCreate(
            url=url,
            discovered_from=discovered_from,
            proposed_type=proposed_type,
            signals=new_signals,
        ),
    )
