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

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.source import (
    CandidateSource,
    CandidateSourceStatus,
    ScholarshipSource,
    FetchStatus,
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


def get_latest_fetch_log_per_source(db: Session) -> list[SourceFetchLog]:
    """The single most recent fetch-log row of each source, any kind — for coverage
    (002 US4) so it never loads the whole history. `DISTINCT ON` walks the
    `source_id` index; bounded at one row per source."""
    stmt = (
        select(SourceFetchLog)
        .distinct(SourceFetchLog.source_id)
        .order_by(SourceFetchLog.source_id, SourceFetchLog.started_at.desc(), SourceFetchLog.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def get_latest_run_fetch_logs(db: Session) -> list[SourceFetchLog]:
    """Every row belonging to each source's most recent MONITORING RUN (ADR-0008:
    one check is several rows, outcome is per run). Rows with a NULL
    `monitoring_run_id` are ignored. Bounded at a few rows per source."""
    latest_run = (
        select(SourceFetchLog.source_id, SourceFetchLog.monitoring_run_id)
        .where(SourceFetchLog.monitoring_run_id.is_not(None))
        .distinct(SourceFetchLog.source_id)
        .order_by(SourceFetchLog.source_id, SourceFetchLog.started_at.desc(), SourceFetchLog.id.desc())
        .subquery()
    )
    stmt = select(SourceFetchLog).join(
        latest_run,
        and_(
            SourceFetchLog.source_id == latest_run.c.source_id,
            SourceFetchLog.monitoring_run_id == latest_run.c.monitoring_run_id,
        ),
    )
    return list(db.execute(stmt).scalars().all())


def count_pending_candidate_sources(db: Session) -> int:
    return db.execute(
        select(func.count()).select_from(CandidateSource).where(CandidateSource.status == CandidateSourceStatus.PENDING)
    ).scalar_one()


def get_source_by_id(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    stmt = select(SourceRegistry).where(
        SourceRegistry.id == source_id, SourceRegistry.status == SourceStatus.ACTIVE
    )
    return db.execute(stmt).scalar_one_or_none()


_MONITORABLE_STATUSES = (SourceStatus.ACTIVE, SourceStatus.FAILING)


def get_monitorable_sources(
    db: Session,
    *,
    discovery_role: bool | None = None,
    access_method: str | None = None,
) -> list[SourceRegistry]:
    """002 US3 (ADR-0008): the MONITOR's selection query — `ACTIVE | FAILING`, so a
    failing source stays scheduled and can recover (O-2). `PENDING` and `DISABLED`
    are never returned. `get_active_sources` is unchanged and remains the gate for
    discovery, Q&A, matching and search."""
    stmt = select(SourceRegistry).where(SourceRegistry.status.in_(_MONITORABLE_STATUSES))
    if discovery_role is not None:
        stmt = stmt.where(SourceRegistry.discovery_role == discovery_role)
    if access_method is not None:
        stmt = stmt.where(SourceRegistry.access_method == access_method)
    return list(db.execute(stmt).scalars().all())


def get_monitorable_source_by_id(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    """Monitor-only counterpart of `get_source_by_id` (`ACTIVE | FAILING`); backs the
    connectors' `allow_failing=True` gate."""
    stmt = select(SourceRegistry).where(
        SourceRegistry.id == source_id, SourceRegistry.status.in_(_MONITORABLE_STATUSES)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_source_any_status(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    """Read-only lookup regardless of status — for the health/fetch-log views only.
    Never used to gate a fetch."""
    return db.get(SourceRegistry, source_id)


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


def reactivate_source(db: Session, source_id: uuid.UUID) -> SourceRegistry | None:
    """002 O-2 recovery: `failing -> active` ONLY. A `disabled`/`pending`/`active`
    source is returned unchanged — recovery never resurrects an operator-disabled
    or unapproved source."""
    source = db.get(SourceRegistry, source_id)
    if source is None:
        return None
    if source.status == SourceStatus.FAILING:
        source.status = SourceStatus.ACTIVE
        db.commit()
        db.refresh(source)
    return source


# Upper bound on how many recent monitoring runs per source the streak scan reads.
_FAILURE_SCAN_RUNS = 100


def _failure_streaks(db: Session, source_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, int]:
    """ADR-0008: consecutive failed monitoring-RUN outcomes per source (absent = 0).

    One outcome per (source, run): failed if ANY of that source's rows in the run is
    not `OK` (a failed check is an `OK` HTTP row plus a `FAIL` row). Rows with a NULL
    `monitoring_run_id` (discovery / ingestion stage rows) are ignored. Runs are
    ordered newest-first by their latest row; a single set-based query."""
    per_run = (
        select(
            SourceFetchLog.source_id.label("source_id"),
            func.max(SourceFetchLog.started_at).label("last_at"),
            func.bool_or(SourceFetchLog.status != FetchStatus.OK).label("failed"),
        )
        .where(SourceFetchLog.monitoring_run_id.is_not(None))
        .group_by(SourceFetchLog.source_id, SourceFetchLog.monitoring_run_id)
    )
    if source_ids is not None:
        per_run = per_run.where(SourceFetchLog.source_id.in_(source_ids))
    per_run = per_run.subquery()
    ranked = select(
        per_run.c.source_id,
        per_run.c.failed,
        func.row_number().over(partition_by=per_run.c.source_id, order_by=per_run.c.last_at.desc()).label("rn"),
    ).subquery()
    rows = db.execute(
        select(ranked.c.source_id, ranked.c.failed)
        .where(ranked.c.rn <= _FAILURE_SCAN_RUNS)
        .order_by(ranked.c.source_id, ranked.c.rn)
    ).all()

    streaks: dict[uuid.UUID, int] = {}
    ended: set[uuid.UUID] = set()
    for source_id, failed in rows:
        if source_id in ended:
            continue
        if failed:
            streaks[source_id] = streaks.get(source_id, 0) + 1
        else:
            ended.add(source_id)
    return streaks


def consecutive_failure_count(db: Session, source_id: uuid.UUID) -> int:
    return _failure_streaks(db, [source_id]).get(source_id, 0)


def list_source_health(db: Session, status: SourceStatus | None = None) -> list[tuple[SourceRegistry, int]]:
    """Every non-`pending` registry source with its derived failure streak, ordered by
    name. `pending` is excluded (ADR-0008); `disabled` is included."""
    stmt = select(SourceRegistry).where(SourceRegistry.status != SourceStatus.PENDING)
    if status is not None:
        stmt = stmt.where(SourceRegistry.status == status)
    sources = list(db.execute(stmt.order_by(SourceRegistry.name, SourceRegistry.id)).scalars().all())
    if not sources:
        return []
    streaks = _failure_streaks(db, [source.id for source in sources])
    return [(source, streaks.get(source.id, 0)) for source in sources]


def get_recent_fetch_logs(db: Session, source_id: uuid.UUID, limit: int = 50) -> list[SourceFetchLog]:
    """Newest-first, `limit`-bounded (never loads a source's whole history)."""
    stmt = (
        select(SourceFetchLog)
        .where(SourceFetchLog.source_id == source_id)
        .order_by(SourceFetchLog.started_at.desc(), SourceFetchLog.id.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def log_fetch(db: Session, data: SourceFetchLogCreate) -> SourceFetchLog:
    payload = data.model_dump(exclude_unset=False)
    if payload.get("started_at") is None:
        # Application clock, not the DB `now()` default: that is the TRANSACTION
        # start time, so rows written in one transaction tie, and the per-run
        # health streak (ADR-0008) needs strictly ordered timestamps.
        payload["started_at"] = datetime.now(timezone.utc)
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
