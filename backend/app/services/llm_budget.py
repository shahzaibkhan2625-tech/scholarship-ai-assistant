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

__all__ = ["BudgetConfig", "Decision", "DbLlmGate", "budget_day", "next_boundary", "decide", "record_avoided"]

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
