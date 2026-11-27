"""The database-backed LLM budget gate (003, ADR-0014, research R-02/R-04).

`decide` is the pure admission policy; `DbLlmGate` applies it under an advisory transaction lock on
its own short session, so counting is exact across workers and restarts (FR-BUDGET-8). The session
factory and the clock are injectable (tests use the rollback factory and a fake clock).

Ladder (defaults A=20, R=8): user-facing is admitted while any allowance remains; a background class
needs the remaining allowance to stay above reserve + its offset (backfill 0, monitoring 2, validation
4), and backfill is also capped at a daily share. A refused background request is deferred, a refused
student request is denied; both are counted. Failures of the budget store defer background work and
let student requests through to the provider own limit (FR-BUDGET-9, R-04).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.budget_day import budget_day, next_boundary
from app.core.config import Settings
from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmError,
    LlmQuotaExhausted,
    LlmTemporarilyUnavailable,
    Priority,
    RequestType,
    current_priority,
)
from app.data.repositories import llm_budget_repo as repo

__all__ = [
    "BudgetConfig",
    "BudgetSnapshot",
    "DbLlmGate",
    "Decision",
    "SourceCadenceRow",
    "budget_day",
    "budget_snapshot",
    "decide",
    "next_boundary",
    "record_avoided",
    "source_cadence_rows",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BudgetConfig:
    allowance: dict[RequestType, int | None]
    reserve: int
    offsets: dict[Priority, int]
    backfill_share: int
    tz: ZoneInfo
    default_pause_seconds: int

    @classmethod
    def from_settings(cls, s: Settings) -> "BudgetConfig":
        return cls(
            allowance={
                RequestType.GENERATE: s.llm_generation_daily_allowance,
                RequestType.EMBED: s.llm_embedding_daily_allowance,
            },
            reserve=s.llm_user_facing_reserve,
            offsets={
                Priority.BACKFILL: s.llm_ladder_offset_backfill,
                Priority.MONITORING: s.llm_ladder_offset_monitoring,
                Priority.VALIDATION: s.llm_ladder_offset_validation,
            },
            backfill_share=s.llm_backfill_daily_share,
            tz=ZoneInfo(s.llm_budget_timezone),
            default_pause_seconds=s.llm_default_pause_seconds,
        )


@dataclass(frozen=True)
class Decision:
    admit: bool
    event: str  # allowed | deferred | denied
    retry_at: datetime | None = None
    reason: str | None = None


_ADMIT = Decision(True, "allowed")


def decide(
    config: BudgetConfig,
    request_type: RequestType,
    priority: Priority,
    *,
    allowed_total: int,
    backfill_allowed: int,
    paused_until: datetime | None,
    now: datetime,
) -> Decision:
    if paused_until is not None and paused_until > now:
        if priority is Priority.USER_FACING:
            return Decision(False, "denied", paused_until, "provider_quota")
        return Decision(False, "deferred", paused_until, "provider_pause")
    allowance = config.allowance.get(request_type)
    if allowance is None:  # counted, not limited
        return _ADMIT
    boundary = next_boundary(now, config.tz)
    remaining = allowance - allowed_total
    if priority is Priority.USER_FACING:
        if remaining > 0:
            return _ADMIT
        return Decision(False, "denied", boundary, "daily_allowance_spent")
    if remaining <= config.reserve + config.offsets[priority]:
        return Decision(False, "deferred", boundary, "below_floor")
    if priority is Priority.BACKFILL and backfill_allowed >= config.backfill_share:
        return Decision(False, "deferred", boundary, "backfill_share")
    return _ADMIT


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DbLlmGate:
    def __init__(
        self,
        config: BudgetConfig,
        *,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if session_factory is None:
            from app.data.repositories.db import SessionLocal

            session_factory = SessionLocal
        self.config = config
        self._session_factory = session_factory
        self._clock = clock
        self.store_failures = 0

    def acquire(self, request_type: RequestType, priority: Priority) -> None:
        now = self._clock()
        day = budget_day(now, self.config.tz)
        rt = request_type.value
        try:
            with self._session_factory() as db:
                repo.advisory_lock(db, day, rt)
                pause = repo.get_pause(db, rt)
                decision = decide(
                    self.config,
                    request_type,
                    priority,
                    allowed_total=repo.allowed_today(db, day, rt),
                    backfill_allowed=repo.count(db, day, rt, Priority.BACKFILL.value, repo.ALLOWED),
                    paused_until=pause.paused_until if pause is not None else None,
                    now=now,
                )
                repo.increment(db, day, rt, priority.value, decision.event)
                db.commit()
        except Exception as exc:  # noqa: BLE001 - the budget store must never break the caller
            self.store_failures += 1
            logger.warning("llm budget store unavailable (%s)", type(exc).__name__)
            if priority is Priority.USER_FACING:
                return  # proceeds to the provider own limit
            raise LlmBudgetDeferred(now + timedelta(seconds=self.config.default_pause_seconds)) from None
        if decision.admit:
            return
        if priority is Priority.USER_FACING:
            raise LlmTemporarilyUnavailable(decision.retry_at, decision.reason)
        raise LlmBudgetDeferred(decision.retry_at)

    def report_provider_error(self, request_type: RequestType, error: LlmError) -> None:
        if not isinstance(error, LlmQuotaExhausted):
            return
        now = self._clock()
        day = budget_day(now, self.config.tz)
        rt = request_type.value
        priority = (current_priority() or Priority.USER_FACING).value
        until = error.retry_at or now + timedelta(seconds=self.config.default_pause_seconds)
        allowance = self.config.allowance.get(request_type)
        try:
            with self._session_factory() as db:
                repo.advisory_lock(db, day, rt)
                repo.increment(db, day, rt, priority, "provider_exhausted")
                if allowance is not None and repo.allowed_today(db, day, rt) < allowance:
                    repo.increment(db, day, rt, priority, "discrepancy")
                repo.set_pause(db, rt, until, "daily_quota" if error.daily else "rate_limit", error.observed_limit)
                db.commit()
        except Exception as exc:  # noqa: BLE001
            self.store_failures += 1
            logger.warning("llm budget store unavailable (%s)", type(exc).__name__)


def record_avoided(
    db: Session,
    event: str,
    *,
    request_type: RequestType = RequestType.GENERATE,
    now: datetime | None = None,
) -> None:
    """Count a provider request that was NOT made (`avoided_unchanged`, `avoided_rules`) in the caller's
    transaction (FR-OBS-1). Best effort inside a savepoint: counting must never break a check."""
    from app.core.config import settings

    moment = now or _utcnow()
    day = budget_day(moment, ZoneInfo(settings.llm_budget_timezone))
    priority = (current_priority() or Priority.MONITORING).value
    try:
        with db.begin_nested():
            repo.increment(db, day, request_type.value, priority, event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not count %s (%s)", event, type(exc).__name__)


# -- read side: one set of helpers behind both `/metrics` and `GET /monitoring/budget` (FR-OBS-1/5, R-18) -----------

LEDGER_EVENTS = ("deferred", "denied", "provider_exhausted", "discrepancy", "avoided_unchanged", "avoided_rules")
_CLASSES = tuple(p.value for p in Priority)


@dataclass(frozen=True)
class RequestTypeView:
    allowance: int | None  # None: counted, not limited
    reserve: int | None  # the user-facing reserve applies to generation only
    used: dict[str, int]  # today's `allowed` count per priority class
    remaining: int | None
    events: dict[str, int]  # today's count per event, summed over classes (LEDGER_EVENTS)
    events_by_class: dict[tuple[str, str], int]  # (class, event) -> today's count
    paused_until: datetime | None
    observed_limit: int | None


@dataclass(frozen=True)
class BudgetSnapshot:
    budget_day: date
    reset_at: datetime
    seconds_to_reset: float
    classify_pending: int
    request_types: dict[str, RequestTypeView]


def budget_snapshot(db: Session, *, now: datetime | None = None, config: BudgetConfig | None = None) -> BudgetSnapshot:
    """Today's numbers, read from the ledger, the pause rows and the classification queue (reads only)."""
    from app.core.config import settings
    from app.data.repositories import classify_pending_repo

    config = config or BudgetConfig.from_settings(settings)
    moment = now or _utcnow()
    day = budget_day(moment, config.tz)
    reset_at = next_boundary(moment, config.tz)
    counts = repo.day_counts(db, day)
    views: dict[str, RequestTypeView] = {}
    for request_type in RequestType:
        rt = request_type.value
        used = {cls: counts.get((rt, cls, repo.ALLOWED), 0) for cls in _CLASSES}
        allowance = config.allowance.get(request_type)
        pause = repo.get_pause(db, rt)
        paused_until = pause.paused_until if pause is not None and pause.paused_until and pause.paused_until > moment else None
        views[rt] = RequestTypeView(
            allowance=allowance,
            reserve=config.reserve if request_type is RequestType.GENERATE else None,
            used=used,
            remaining=None if allowance is None else allowance - sum(used.values()),
            events={ev: sum(counts.get((rt, cls, ev), 0) for cls in _CLASSES) for ev in LEDGER_EVENTS},
            events_by_class={
                (cls, ev): counts[(rt, cls, ev)] for cls in _CLASSES for ev in LEDGER_EVENTS if (rt, cls, ev) in counts
            },
            paused_until=paused_until,
            observed_limit=pause.observed_limit if pause is not None and paused_until is not None else None,
        )
    return BudgetSnapshot(
        budget_day=day,
        reset_at=reset_at,
        seconds_to_reset=(reset_at - moment).total_seconds(),
        classify_pending=classify_pending_repo.size(db),
        request_types=views,
    )


@dataclass(frozen=True)
class SourceCadenceRow:
    domain: str
    interval_minutes: int
    interval_source: str  # derived | override | default
    last_checked_at: datetime | None
    next_due_at: datetime | None
    noisy: bool
    rules_review_needed: bool


_INTERVAL_SOURCE = {"frequency": "derived", "override": "override", "default": "default"}


def source_cadence_rows(db: Session, *, now: datetime | None = None) -> list[SourceCadenceRow]:
    """One row per monitored source: the interval in force, when it is next due, and the state flags."""
    from app.core.config import settings
    from app.data.repositories import check_state_repo, source_repo
    from app.services import cadence, content_fingerprint

    states = check_state_repo.get_all(db)
    rows = []
    for source in source_repo.get_monitorable_sources(db, discovery_role=True, access_method="web"):
        state = states.get(source.id)
        interval = cadence.check_interval(source)
        gate = state.next_attempt_at if state is not None else None
        never = source.last_checked_at is None and gate is None
        rows.append(
            SourceCadenceRow(
                domain=source.domain,
                interval_minutes=interval.minutes,
                interval_source=_INTERVAL_SOURCE[interval.origin],
                last_checked_at=source.last_checked_at,
                next_due_at=None if never else cadence.next_due_at(source, state),
                noisy=state is not None
                and content_fingerprint.is_noisy(
                    state.checks_window, state.changed_window,
                    window=settings.noise_window_checks, threshold=settings.noise_threshold,
                ),
                rules_review_needed=bool(state.rules_review_needed) if state is not None else False,
            )
        )
    return sorted(rows, key=lambda r: r.domain)
