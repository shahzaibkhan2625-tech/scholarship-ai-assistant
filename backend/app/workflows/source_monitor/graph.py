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
grounded-and-seen scholarship -> stale sweep -> counters. After the lock is
released, the US2 alert step re-matches and alerts owners (`_alert_step`).

**Source health (US3, ADR-0008):** health is derived, not stored. After each
check, `_apply_health_transition` flips an `active` source to `failing` once
`source_repo.consecutive_failure_count` (failed monitoring-RUN outcomes only)
reaches `HEALTH_WINDOW_N`, and restores a `failing` source to `active` on one
successful check — BEFORE change detection, so ingestion's source gate passes.
Failing sources stay scheduled (`allow_failing=True`); `pending`/`disabled` never
are. A crash after the fetch is recorded as a `FAIL` row for the run, so it is
counted rather than reading as healthy.

**Lifecycle (US5, ADR-0009):** a matched, grounded, open listing item from an approved
source confirms the record (`scholarship_repo.mark_confirmed_by_monitoring`; CONFLICTING
rows are never touched) BEFORE its transition is evaluated, so `newly_discovered` /
`stale` / `source_unavailable` / `unverified` records land on `verified`. A field change
in the same check wins: `updated` beats the verified exit. `run_source_monitor(now=...)`
is a clock seam (default `datetime.now(timezone.utc)`) for deterministic tests.

**Observability (US8, ADR-0011):** a run binds `run_id` (and each source `source_id`) into
the log context, emits `monitoring run started` / `source check finished` /
`monitoring run finished` events, and increments `monitoring_runs_total{status}`,
`source_fetch_total{status}` and the run-duration histogram (`app.core.metrics`).

**Candidate pre-checks (US7 T244, ADR-0010):** after the source loop and before the run is
finished, `_validate_candidates_step` validates up to `MAX_CANDIDATE_VALIDATIONS_PER_RUN`
pending candidates that have no validation or one older than
`CANDIDATE_VALIDATION_RECHECK_DAYS`. It ends its read transaction before any network / LLM
call, commits per candidate (inside the service), takes no lock, logs and continues on any
exception, and never reads or writes candidate status: it is advisory only.

**Sanitization (US8 T236, NFR-HARDEN-8):** the extractor already strips tags from the page
before the LLM sees it, but only the candidate NAME is grounded against that text; the other
string fields are model output. `_sanitize_candidate` therefore strips markup and
script-scheme URLs from every string field of a candidate at this ingest boundary, before
it is diffed or stored. Clean values pass through byte-for-byte (no spurious diffs).

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

import functools
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable, Mapping

from sqlalchemy.orm import Session

from app.core import metrics
from app.core.config import settings
from app.core.logging import bind_log_context, log_context, reset_log_context
from app.data.repositories import candidate_validation_repo, monitoring_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringStatus, MonitoringTrigger
from app.models.scholarship import LifecycleStatus, Scholarship
from app.models.source import FetchStatus, SourceRegistry, SourceStatus
from app.schemas.source import SourceFetchLogCreate
from app.scheduling.locks import source_lock
from app.services import alerts as alerts_service
from app.services import candidate_validation as candidate_validation_service
from app.services import lifecycle as lifecycle_service
from app.services import verification as verification_service
from app.services.alerts import ChangeEvent, NotificationChannel, RematchBudget
from app.services.lifecycle import FetchOutcome, ListedState
from app.services.listing_diff import diff_listing_fields
from app.sources.connectors.official_fetch import (
    FAILURE_FETCH_ERROR,
    ListingExtractionResult,
    fetch_and_extract_listing,
    resolve_fetch_target,
)
from app.tools.extract_listing import ExtractListingOutput, clean_html_to_text, extract_listing_from_page
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
    # "ok" | "fail" | "error" (the check crashed); drives the log event and the fetch counter.
    fetch_status: str | None = None
    # US2: one event per stored new/field-changed scholarship, consumed by the
    # alert step after the source's work has committed.
    events: list[ChangeEvent] = field(default_factory=list)


_SCRIPT_SCHEME_RE = re.compile(r"\b(?:javascript|vbscript)\s*:", re.IGNORECASE)


def _sanitize_text(value: str) -> str:
    """Strips `<script>`/`<style>` blocks and every tag (inline event handlers live inside
    tags, so they go with them) and removes script-scheme prefixes. A value with neither
    markup nor a script scheme is returned unchanged."""
    if "<" not in value and not _SCRIPT_SCHEME_RE.search(value):
        return value
    return _SCRIPT_SCHEME_RE.sub("", clean_html_to_text(value)).strip()


def _sanitize_candidate(candidate: dict) -> tuple[dict, list[str]]:
    """T236. Returns (candidate with every string field sanitized, names of the fields
    that changed). A field emptied by sanitization becomes `None`."""
    cleaned = dict(candidate)
    changed: list[str] = []
    for key, value in candidate.items():
        if isinstance(value, str):
            safe = _sanitize_text(value)
            if safe != value:
                cleaned[key] = safe or None
                changed.append(key)
    return cleaned, changed


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _expired_by_deadline(scholarship: Scholarship, now: datetime) -> bool:
    """EXPIRED is derived from the deadline (`compute_lifecycle_status`), so it
    is re-derived against the stored (post-update) deadline rather than being
    treated as terminal."""
    status = verification_service.compute_lifecycle_status(
        verification_service.VerificationInput(
            retrieved_at=scholarship.retrieved_at,
            last_verified_at=scholarship.last_verified_at,
            deadline=scholarship.deadline,
            official_source_confirmed=False,
            now=now,
        )
    )
    return status == LifecycleStatus.EXPIRED


def _matched_transition(
    scholarship: Scholarship,
    listed: ListedState,
    *,
    changed: bool,
    changed_fields: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> LifecycleStatus | None:
    """UPDATED/CLOSED/REOPENED for a record found in the listing. The
    lifecycle service never emits UPDATED (that is this diff step's job), so a
    field change yields UPDATED unless the service says REOPENED. CLOSED is
    terminal (an explicit signal); EXPIRED is derived, so a deadline change
    that moves it into the future lifts it to UPDATED. US5 Q4 (ADR-0009): when a
    field change and a confirming re-sighting coincide, UPDATED wins over the
    VERIFIED/UNVERIFIED exit."""
    target = lifecycle_service.evaluate_fetch_outcome(
        scholarship, FetchOutcome(fetch_succeeded=True, extraction_grounded=True, listed_state=listed)
    )
    if not changed or listed != ListedState.OPEN:
        return target
    if target == LifecycleStatus.REOPENED:
        return target
    if scholarship.lifecycle_status == LifecycleStatus.CLOSED:
        return None
    if scholarship.lifecycle_status == LifecycleStatus.EXPIRED:
        if "deadline" in changed_fields and not _expired_by_deadline(scholarship, now or _utcnow()):
            return LifecycleStatus.UPDATED
        return None
    return LifecycleStatus.UPDATED


def _detect_changes(
    db: Session,
    source: SourceRegistry,
    result: ListingExtractionResult,
    stored: list[Scholarship],
    stats: _SourceStats,
    now: datetime | None = None,
) -> tuple[set[uuid.UUID], set[str]]:
    """T179. Returns (grounded-and-seen scholarship ids, normalized names seen
    in the listing)."""
    by_name: dict[str, Scholarship] = {}
    for scholarship in sorted(stored, key=_recency):  # ascending: the most recent assignment wins
        by_name[_normalized_name(scholarship.name)] = scholarship

    seen_ids: set[uuid.UUID] = set()
    listed_names: set[str] = set()
    for raw_candidate in result.candidates:
        candidate, sanitized_fields = _sanitize_candidate(raw_candidate)
        if sanitized_fields:
            logger.warning("sanitized externally-sourced listing fields", extra={"fields": sanitized_fields})
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
            stats.events.append(ChangeEvent(scholarship_id=created.id, kind="new"))
            continue

        changed_fields = diff_listing_fields(match, candidate)
        changed = bool(changed_fields)
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
            stats.events.append(ChangeEvent(scholarship_id=match.id, kind="updated", fields=tuple(sorted(changed_fields))))
        seen_ids.add(match.id)

        listed = ListedState.CLOSED if _is_explicit_closed(candidate) else ListedState.OPEN
        if listed == ListedState.OPEN and scholarship_repo.mark_confirmed_by_monitoring(db, [match.id]):
            db.refresh(match)  # US5 G1: confirmed BEFORE the transition reads verification_status
        target = _matched_transition(
            match, listed, changed=changed, changed_fields=frozenset(changed_fields), now=now
        )
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
                target = lifecycle_service.evaluate_fetch_outcome(scholarship, outcome)
                if target == LifecycleStatus.UNVERIFIED:
                    # US5 G3: don't flap through UNVERIFIED when freshness would put it straight back.
                    preview = SimpleNamespace(
                        lifecycle_status=target, verification_status=scholarship.verification_status,
                        last_verified_at=scholarship.last_verified_at,
                    )
                    target = lifecycle_service.evaluate_freshness(preview, source, now) or target
                if _write_lifecycle(db, scholarship, target) and target == LifecycleStatus.CLOSED:
                    stats.changes += 1
        else:
            _write_lifecycle(
                db, scholarship, lifecycle_service.evaluate_fetch_outcome(scholarship, _failure_outcome(result.failure_kind))
            )

        _write_lifecycle(db, scholarship, lifecycle_service.evaluate_freshness(scholarship, source, now))


def _apply_health_transition(db: Session, source_id: uuid.UUID, *, success: bool) -> None:
    """US3 / ADR-0008: status follows the derived streak. `disabled` and
    `pending` are never touched; one failure alone never flips (D5)."""
    source = db.get(SourceRegistry, source_id)
    if source is None:
        return
    db.refresh(source)
    if success:
        if source.status == SourceStatus.FAILING:
            source_repo.reactivate_source(db, source_id)
            logger.info("source %s recovered: failing -> active", source_id)
    elif source.status == SourceStatus.ACTIVE:
        failures = source_repo.consecutive_failure_count(db, source_id)
        if failures >= settings.health_window_n:
            source_repo.mark_source_failing(db, source_id)
            logger.warning("source %s marked failing after %d consecutive failed runs", source_id, failures)


def _record_crashed_check(db: Session, run: MonitoringRun, source_id: uuid.UUID, exc: Exception) -> None:
    """A check that raised after the fetch left only the `OK` HTTP row, which reads
    as healthy. Record it as the failed outcome it is (FR-HEALTH-1/2). Best effort:
    bookkeeping trouble is logged and must never stop the run."""
    try:
        source_repo.log_fetch(
            db,
            SourceFetchLogCreate(
                source_id=source_id,
                status=FetchStatus.FAIL,
                error=f"[monitor] {type(exc).__name__}: {exc}",
                retry_count=0,
                monitoring_run_id=run.id,
            ),
        )
        source_repo.touch_source_check(db, source_id, success=False)
        _apply_health_transition(db, source_id, success=False)
    except Exception:  # noqa: BLE001
        logger.exception("could not record failed check for source %s", source_id)
        db.rollback()


def _monitor_one_source(
    db: Session,
    run: MonitoringRun,
    source_id: uuid.UUID,
    stats: _SourceStats,
    *,
    fetch: Callable[[str], FetchResult],
    extract_listing: Callable[..., ExtractListingOutput],
    sleep: Callable[[float], None] | None,
    clock: Callable[[], datetime] = _utcnow,
) -> bool:
    """Returns False when the source was skipped (lock held / no longer
    active), True when it was processed."""
    with source_lock(db, source_id) as acquired:
        if not acquired:
            logger.info("source %s skipped: lock held by another monitor", source_id)
            return False
        source = source_repo.get_monitorable_source_by_id(db, source_id)
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
            allow_failing=True,
            fetch=fetch,
            extract_listing=extract_listing,
            sleep=sleep,
        )
        success = result.status == "ok"
        stats.fetch_failed = not success
        stats.fetch_status = "ok" if success else "fail"
        source_repo.touch_source_check(db, source_id, success=success)
        _apply_health_transition(db, source_id, success=success)  # recovery must precede ingestion's source gate

        now = clock()
        listed_names: set[str] = set()
        if success:
            stored = scholarship_repo.list_for_source(db, source_id)
            seen_ids, listed_names = _detect_changes(db, source, result, stored, stats, now)
            scholarship_repo.mark_seen_verified(db, seen_ids, now)  # D4, before the sweep

        _stale_sweep(db, source, result, listed_names=listed_names, baseline=baseline, now=now, stats=stats)
        return True


def _alert_step(
    db: Session,
    run: MonitoringRun,
    stats: _SourceStats,
    budget: RematchBudget,
    channels: Mapping[str, NotificationChannel] | None,
) -> None:
    """US2 re-match/alert for one source's stored changes (ADR-0007).

    Deliberately called AFTER `_monitor_one_source` has returned, i.e. outside
    `source_lock`: the lock is a transaction on its own connection against a
    pooled Neon endpoint, and holding it open across per-owner LLM calls and
    SMTP round-trips would pin a pooled backend for the slowest external call.
    It is safe to run unlocked because the events come only from this run,
    which held the lock while it stored them. A failure here is logged only and
    can never undo the already-committed change.
    """
    if not stats.events:
        return
    try:
        alerts_service.process_changes(db, run.id, stats.events, budget=budget, channels=channels)
    except Exception:  # noqa: BLE001 - alerting must never fail the run or the stored change
        logger.exception("alert step failed for run %s; stored changes are unaffected", run.id)
        db.rollback()


def _validate_candidates_step(
    db: Session, validate: Callable[[Session, uuid.UUID], object], clock: Callable[[], datetime]
) -> None:
    """US7 T244 / ADR-0010. Advisory pre-checks for pending candidate sources.

    The read transaction ends (`commit`) before the first network / LLM call, so no
    pooled-Neon backend idles inside a transaction across a slow fetch; each
    validation commits its own short insert. No advisory lock: overlapping runs can at
    worst append a duplicate advisory row. Any failure is logged and skipped; this step
    can never fail the run and never touches candidate status."""
    try:
        stale_before = clock() - timedelta(days=settings.candidate_validation_recheck_days)
        candidate_ids = candidate_validation_repo.list_pending_needing_validation(
            db, stale_before=stale_before, limit=settings.max_candidate_validations_per_run
        )
        db.commit()
    except Exception:  # noqa: BLE001 - advisory step must never fail the run
        logger.exception("candidate validation selection failed; skipping the step")
        db.rollback()
        return
    for candidate_id in candidate_ids:
        try:
            validate(db, candidate_id)
        except Exception:  # noqa: BLE001 - one bad candidate must not stop the rest
            logger.exception("validation of candidate %s failed; continuing", candidate_id)
            db.rollback()


def _record_run_end(status: MonitoringStatus, counters: _Counters, started: float) -> None:
    duration = time.monotonic() - started
    metrics.MONITORING_RUNS_TOTAL.labels(status=status.value).inc()
    metrics.MONITORING_RUN_DURATION_SECONDS.observe(duration)
    logger.log(
        logging.INFO if status == MonitoringStatus.COMPLETED else logging.ERROR,
        "monitoring run finished",
        extra={
            "status": status.value,
            "sources_processed": counters.processed,
            "sources_failed": counters.failed,
            "changes_detected": counters.changes,
            "duration_seconds": round(duration, 3),
        },
    )


def run_source_monitor(
    db: Session,
    *,
    trigger: MonitoringTrigger,
    run_id: uuid.UUID | None = None,
    fetch: Callable[[str], FetchResult] = fetch_url,
    extract_listing: Callable[..., ExtractListingOutput] = extract_listing_from_page,
    sleep: Callable[[float], None] | None = None,
    alert_channels: Mapping[str, NotificationChannel] | None = None,
    rematch_budget: RematchBudget | None = None,
    now: Callable[[], datetime] | None = None,
    validate_candidate: Callable[[Session, uuid.UUID], object] | None = None,
) -> MonitoringRun:
    run = monitoring_repo.get_run(db, run_id) if run_id is not None else monitoring_repo.create_run(db, trigger)
    if run is None:
        raise ValueError(f"monitoring run {run_id} not found")

    run_token = bind_log_context(run_id=run.id)
    started = time.monotonic()
    logger.info("monitoring run started", extra={"trigger": run.trigger.value})
    validator = validate_candidate or functools.partial(
        candidate_validation_service.validate_candidate, fetch=fetch, extract_listing=extract_listing
    )
    counters = _Counters()
    budget = rematch_budget if rematch_budget is not None else RematchBudget(settings.max_alert_rematches_per_run)
    finished = False
    try:
        sources = source_repo.get_monitorable_sources(db, discovery_role=True, access_method="web")
        for source_id in [source.id for source in sources]:
            with log_context(source_id=source_id):
                stats = _SourceStats()
                try:
                    processed = _monitor_one_source(
                        db, run, source_id, stats, fetch=fetch, extract_listing=extract_listing, sleep=sleep,
                        clock=now or _utcnow,
                    )
                except Exception as exc:  # noqa: BLE001 - one bad source must not stop the run
                    logger.exception("source %s failed during monitoring", source_id)
                    db.rollback()
                    processed, stats.fetch_failed, stats.fetch_status = True, True, "error"
                    _record_crashed_check(db, run, source_id, exc)
                if not processed:
                    continue
                _alert_step(db, run, stats, budget, alert_channels)
                counters.processed += 1
                counters.failed += 1 if stats.fetch_failed else 0
                counters.changes += stats.changes
                fetch_status = stats.fetch_status or ("fail" if stats.fetch_failed else "ok")
                metrics.SOURCE_FETCH_TOTAL.labels(status=fetch_status).inc()
                logger.log(
                    logging.WARNING if stats.fetch_failed else logging.INFO,
                    "source check finished",
                    extra={"fetch_status": fetch_status, "changes": stats.changes},
                )
                monitoring_repo.update_run_counters(
                    db,
                    run.id,
                    sources_processed=counters.processed,
                    sources_failed=counters.failed,
                    changes_detected=counters.changes,
                )
        _validate_candidates_step(db, validator, now or _utcnow)
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
        try:
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
            _record_run_end(MonitoringStatus.COMPLETED if finished else MonitoringStatus.FAILED, counters, started)
        finally:
            reset_log_context(run_token)
    db.refresh(run)
    return run
