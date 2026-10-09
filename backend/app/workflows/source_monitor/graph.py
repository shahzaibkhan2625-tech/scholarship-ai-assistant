"""`source_monitor` workflow (002 US1, FR-MON-1..6) — periodic re-fetch of
every active web discovery source, change detection against what is already
stored, and lifecycle upkeep.

**Plain Python orchestration, NOT LangGraph and NOT an agent**, despite living
in a `graph.py` like the other workflow folders (confirmed decision, Option A:
a per-source loop with locks and `finally` blocks is not a node graph; no LLM
call is made here directly — the only LLM touch is the existing
`extract_listing` seam inside `fetch_and_extract_listing`). No agent imports.

Per source, in order, inside that source's advisory lock (see
`scheduling/locks.py` for the transaction-scoped lock design):
re-check still active -> fetch (`bypass_cache=True`,
`skip_failing_transition=True`) -> `touch_source_check` -> change detection
(or the failed-fetch lifecycle outcome) -> D4 freshness refresh for every
grounded-and-seen scholarship -> stale sweep -> counters.

**FR-MON-3 coverage gap (named, not hidden):** only `deadline`,
`funding_status` and `degree_level` changes are detectable
(`services/listing_diff.py`). "Requirement" changes cannot be detected
because the listing extractor never emits requirements data.

**Known limitation:** stored scholarships are matched to extracted candidates
by `name.strip().lower()`. If several stored rows of one source share a
normalized name, the most recently updated one is used (`last_verified_at`,
else `retrieved_at` — `scholarships` has no `updated_at` column); the others
are not silently reconciled any other way.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.data.repositories import monitoring_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringStatus, MonitoringTrigger
from app.models.scholarship import LifecycleStatus, Scholarship
from app.models.source import FetchStatus, SourceRegistry
from app.scheduling.locks import source_lock
from app.services import lifecycle as lifecycle_service
from app.services.lifecycle import FetchOutcome, ListedState
from app.services.listing_diff import diff_listing_fields
from app.sources.connectors.official_fetch import (
    FAILURE_FETCH_ERROR,
    ListingExtractionResult,
    fetch_and_extract_listing,
    resolve_fetch_target,
)
from app.tools.extract_listing import ExtractListingOutput, extract_listing_from_page
from app.tools.web_fetch import FetchResult, fetch_url
from app.workflows.ingestion.graph import run_ingestion

logger = logging.getLogger(__name__)

__all__ = ["run_source_monitor"]

# D2(c): a run whose accepted_count falls below this fraction of the previous
# successful run's items_found is treated as a suspect (partial) listing and
# can never close records by absence.
_SHRINK_GUARD_RATIO = 0.5


@dataclass
class _Counters:
    processed: int = 0
    failed: int = 0
    changes: int = 0


@dataclass
class _SourceStats:
    changes: int = 0
    fetch_failed: bool = False


def _normalized_name(name: str) -> str:
    # Same normalization as scholarship_repo.find_by_name (strip + lower).
    return name.strip().lower()


def _recency(scholarship: Scholarship) -> datetime:
    return scholarship.last_verified_at or scholarship.retrieved_at


def _previous_items_found(db: Session, source: SourceRegistry) -> int | None:
    """Previous successful extraction's `items_found` for this source+URL
    (read BEFORE this run's own fetch logs a row)."""
    target_url = resolve_fetch_target(source).url
    for log in source_repo.get_fetch_logs_for_source(db, source.id):
        if log.status == FetchStatus.OK and log.items_found is not None and log.fetched_url == target_url:
            return log.items_found
    return None


def _absence_may_close(source: SourceRegistry, accepted_count: int | None, baseline: int | None) -> bool:
    """D2: (a) is the caller's job (success + grounded); this checks (b) the
    operator-asserted `listing_complete` flag and (c) the shrink guard. No
    baseline means the guard cannot pass — absence never closes on a guess."""
    if (source.extraction_rules or {}).get("listing_complete") is not True:
        return False
    if not accepted_count or baseline is None:
        return False
    return accepted_count >= _SHRINK_GUARD_RATIO * baseline


def _is_explicit_closed(candidate: dict) -> bool:
    # Only an explicit signal in the extracted data closes outside D2's gate.
    # The current extractor never emits `closing_status`; this stays inert
    # until it (or an operator-tuned extractor) does.
    return str(candidate.get("closing_status") or "").strip().lower() == "closed"


def _write_lifecycle(db: Session, scholarship: Scholarship, target: LifecycleStatus | None) -> bool:
    if target is None or target == scholarship.lifecycle_status:
        return False
    scholarship.lifecycle_status = target
    db.commit()
    return True


def _matched_transition(scholarship: Scholarship, listed: ListedState, *, changed: bool) -> LifecycleStatus | None:
    """UPDATED/CLOSED/REOPENED for a record found in the listing. The
    lifecycle service never emits UPDATED (that is this diff step's job), so a
    field change yields UPDATED unless the service says REOPENED."""
    target = lifecycle_service.evaluate_fetch_outcome(
        scholarship, FetchOutcome(fetch_succeeded=True, extraction_grounded=True, listed_state=listed)
    )
    if not changed or listed != ListedState.OPEN:
        return target
    if target == LifecycleStatus.REOPENED:
        return target
    if scholarship.lifecycle_status in (LifecycleStatus.CLOSED, LifecycleStatus.EXPIRED):
        return None
    return LifecycleStatus.UPDATED


def _detect_changes(
    db: Session,
    source: SourceRegistry,
    result: ListingExtractionResult,
    stored: list[Scholarship],
    stats: _SourceStats,
) -> tuple[set[uuid.UUID], set[str]]:
    """T179. Returns (grounded-and-seen scholarship ids, normalized names seen
    in the listing)."""
    by_name: dict[str, Scholarship] = {}
    for scholarship in sorted(stored, key=_recency):  # ascending: the most recent assignment wins
        by_name[_normalized_name(scholarship.name)] = scholarship

    seen_ids: set[uuid.UUID] = set()
    listed_names: set[str] = set()
    for candidate in result.candidates:
        name = candidate.get("name")
        if not name or not name.strip():
            continue
        key = _normalized_name(name)
        listed_names.add(key)
        match = by_name.get(key)

        if match is None:
            state = run_ingestion(
                db,
                source_id=source.id,
                source_url=f"https://{source.domain}",
                raw=candidate,
                official_source_confirmed=False,
            )
            created = state.get("scholarship")
            if created is None:
                continue  # ingestion already logged the failure; never silently dropped
            by_name[key] = created  # a duplicate name later in this listing is then "matched"
            seen_ids.add(created.id)
            stats.changes += 1
            continue

        changed = bool(diff_listing_fields(match, candidate))
        if changed:
            state = run_ingestion(
                db,
                source_id=source.id,
                source_url=f"https://{source.domain}",
                raw=candidate,
                official_source_confirmed=False,
                existing_scholarship_id=match.id,
            )
            if state.get("error"):
                continue  # logged by ingestion; no lifecycle write on a failed update
            db.refresh(match)
        seen_ids.add(match.id)

        listed = ListedState.CLOSED if _is_explicit_closed(candidate) else ListedState.OPEN
        target = _matched_transition(match, listed, changed=changed)
        wrote = _write_lifecycle(db, match, target)
        if changed or (wrote and target in (LifecycleStatus.CLOSED, LifecycleStatus.REOPENED)):
            stats.changes += 1

    return seen_ids, listed_names


def _failure_outcome(failure_kind: str | None) -> FetchOutcome:
    """D3: keyed off `failure_kind`, never off error text."""
    if failure_kind == FAILURE_FETCH_ERROR:
        return FetchOutcome(fetch_succeeded=False, retries_exhausted=True)
    if failure_kind is None:
        return FetchOutcome(fetch_succeeded=False)
    # empty_extraction / redirect_anomaly: fetched, but nothing grounded to act on.
    return FetchOutcome(fetch_succeeded=True, extraction_grounded=False)


def _stale_sweep(
    db: Session,
    source: SourceRegistry,
    result: ListingExtractionResult,
    *,
    listed_names: set[str],
    baseline: int | None,
    now: datetime,
    stats: _SourceStats,
) -> None:
    """T180, scoped to this source's stored scholarships only. Runs AFTER the
    D4 refresh, so a record seen this run is never marked stale this run."""
    success = result.status == "ok"
    may_close = success and _absence_may_close(source, result.accepted_count, baseline)

    for scholarship in scholarship_repo.list_for_source(db, source.id):
        if success:
            absent = _normalized_name(scholarship.name) not in listed_names
            if absent:
                outcome = FetchOutcome(
                    fetch_succeeded=True,
                    extraction_grounded=True,
                    listed_state=ListedState.ABSENT if may_close else None,
                )
                if _write_lifecycle(db, scholarship, lifecycle_service.evaluate_fetch_outcome(scholarship, outcome)):
                    stats.changes += 1
        else:
            _write_lifecycle(
                db, scholarship, lifecycle_service.evaluate_fetch_outcome(scholarship, _failure_outcome(result.failure_kind))
            )

        _write_lifecycle(db, scholarship, lifecycle_service.evaluate_freshness(scholarship, source, now))


def _monitor_one_source(
    db: Session,
    run: MonitoringRun,
    source_id: uuid.UUID,
    stats: _SourceStats,
    *,
    fetch: Callable[[str], FetchResult],
    extract_listing: Callable[..., ExtractListingOutput],
    sleep: Callable[[float], None] | None,
) -> bool:
    """Returns False when the source was skipped (lock held / no longer
    active), True when it was processed."""
    with source_lock(db, source_id) as acquired:
        if not acquired:
            logger.info("source %s skipped: lock held by another monitor", source_id)
            return False
        source = source_repo.get_source_by_id(db, source_id)
        if source is None:
            logger.info("source %s skipped: no longer active", source_id)
            return False

        baseline = _previous_items_found(db, source)
        result = fetch_and_extract_listing(
            db,
            source_id,
            None,
            monitoring_run_id=run.id,
            bypass_cache=True,
            skip_failing_transition=True,
            fetch=fetch,
            extract_listing=extract_listing,
            sleep=sleep,
        )
        success = result.status == "ok"
        stats.fetch_failed = not success
        source_repo.touch_source_check(db, source_id, success=success)

        now = datetime.now(timezone.utc)
        listed_names: set[str] = set()
        if success:
            stored = scholarship_repo.list_for_source(db, source_id)
            seen_ids, listed_names = _detect_changes(db, source, result, stored, stats)
            scholarship_repo.mark_seen_verified(db, seen_ids, now)  # D4, before the sweep

        _stale_sweep(db, source, result, listed_names=listed_names, baseline=baseline, now=now, stats=stats)
        return True


def run_source_monitor(
    db: Session,
    *,
    trigger: MonitoringTrigger,
    run_id: uuid.UUID | None = None,
    fetch: Callable[[str], FetchResult] = fetch_url,
    extract_listing: Callable[..., ExtractListingOutput] = extract_listing_from_page,
    sleep: Callable[[float], None] | None = None,
) -> MonitoringRun:
    run = monitoring_repo.get_run(db, run_id) if run_id is not None else monitoring_repo.create_run(db, trigger)
    if run is None:
        raise ValueError(f"monitoring run {run_id} not found")

    counters = _Counters()
    finished = False
    try:
        sources = source_repo.get_active_sources(db, discovery_role=True, access_method="web")
        for source_id in [source.id for source in sources]:
            stats = _SourceStats()
            try:
                processed = _monitor_one_source(
                    db, run, source_id, stats, fetch=fetch, extract_listing=extract_listing, sleep=sleep
                )
            except Exception:  # noqa: BLE001 - one bad source must not stop the run
                logger.exception("source %s failed during monitoring", source_id)
                db.rollback()
                processed, stats.fetch_failed = True, True
            if not processed:
                continue
            counters.processed += 1
            counters.failed += 1 if stats.fetch_failed else 0
            counters.changes += stats.changes
            monitoring_repo.update_run_counters(
                db,
                run.id,
                sources_processed=counters.processed,
                sources_failed=counters.failed,
                changes_detected=counters.changes,
            )
        monitoring_repo.finish_run(
            db,
            run.id,
            status=MonitoringStatus.COMPLETED,
            sources_processed=counters.processed,
            sources_failed=counters.failed,
            changes_detected=counters.changes,
        )
        finished = True
    finally:
        if not finished:
            db.rollback()
            monitoring_repo.finish_run(
                db,
                run.id,
                status=MonitoringStatus.FAILED,
                sources_processed=counters.processed,
                sources_failed=counters.failed,
                changes_detected=counters.changes,
            )
    db.refresh(run)
    return run
