"""Periodic source-monitoring scheduler (002 FR-MON-1).

APScheduler 3.x `BackgroundScheduler`: one interval job, `max_instances=1` and
`coalesce=True` so a slow run never overlaps itself and missed ticks collapse
into one. Each tick is cheap (003 US3): it asks `due_work` whether any source is due or any candidate
needs validation and, if not, returns without creating a `monitoring_runs` row; otherwise it runs
`run_source_monitor(trigger=SCHEDULED)` on its own `SessionLocal()` session. Either way it logs one
structured "monitoring tick" line and then drains the pending-classification queue (003).
`MONITORING_INTERVAL_MINUTES <= 0` disables it.

T230: a second job prunes `source_fetch_log` daily (03:00 UTC, same guards). It is
coupled to the monitoring switch: no scheduler, no retention (no extra setting).
"""

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.data.repositories.db import safe_rollback
from app.models.monitoring import MonitoringTrigger

logger = logging.getLogger(__name__)

__all__ = ["JOB_ID", "RETENTION_JOB_ID", "build_scheduler", "start_scheduler", "stop_scheduler"]

JOB_ID = "source_monitor"
RETENTION_JOB_ID = "fetch_log_retention"

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def _run_scheduled_monitor() -> None:
    from app.data.repositories.db import SessionLocal

    with SessionLocal() as session:
        try:
            _monitoring_tick(session)
        except Exception:  # noqa: BLE001 - a failed tick must never kill the scheduler thread
            logger.exception("scheduled source monitoring run failed")
            safe_rollback(session)
        _drain_classify_queue(session)


def _monitoring_tick(session) -> None:
    """003 FR-CADENCE-1, FR-OBS-3, plan decision 5: an idle tick costs one read and one log line."""
    from app.workflows.source_monitor import graph

    work = graph.due_work(session)
    if work.idle:
        logger.info("monitoring tick", extra={"due": 0, "checked": 0, "skipped": 0, "deferred": 0, "idle": True})
        return
    run = graph.run_source_monitor(session, trigger=MonitoringTrigger.SCHEDULED)
    logger.info(
        "monitoring tick",
        extra={
            "due": work.due,
            "checked": run.sources_processed,
            "skipped": run.sources_skipped,
            "deferred": run.sources_deferred,
            "idle": False,
        },
    )


def _drain_classify_queue(session) -> None:
    """003 FR-BUDGET-13: after the monitor run, give the pending-classification queue its turn under the
    `backfill` class. Runs even when the run failed, and never raises."""
    from app.services import classify_backfill

    try:
        classify_backfill.drain_classify_pending(session)
    except Exception:  # noqa: BLE001 - a failed drain retries on the next tick
        logger.exception("classify queue drain failed")
        safe_rollback(session)


def _run_scheduled_retention() -> None:
    from app.data.repositories.db import SessionLocal
    from app.services import retention

    with SessionLocal() as session:
        try:
            retention.prune_fetch_log(session)  # settings defaults, every source
        except Exception:  # noqa: BLE001 - a failed tick (e.g. Neon cold start) retries next day
            logger.exception("scheduled fetch-log retention failed")
            safe_rollback(session)
        try:
            retention.prune_ledger(session)  # 003: the LLM budget ledger, settings default window
        except Exception:  # noqa: BLE001 - same: retry tomorrow
            logger.exception("scheduled ledger retention failed")
            safe_rollback(session)


def build_scheduler(interval_minutes: int) -> BackgroundScheduler | None:
    """Returns an unstarted scheduler with the monitoring + retention jobs registered, or
    `None` when `interval_minutes <= 0` (disabled)."""
    if interval_minutes <= 0:
        return None
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_scheduled_monitor,
        trigger="interval",
        minutes=interval_minutes,
        id=JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _run_scheduled_retention,
        trigger="cron",
        hour=3,
        minute=0,
        id=RETENTION_JOB_ID,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3 * 3600,
    )
    return scheduler


def _source_overrides() -> list[dict] | None:
    """Per-source interval overrides for the startup record. Best effort: `None` (unknown) when the read
    fails, so a database hiccup at boot never blocks the scheduler."""
    from app.data.repositories import source_repo
    from app.data.repositories.db import SessionLocal
    from app.services import cadence

    try:
        with SessionLocal() as session:
            sources = source_repo.get_monitorable_sources(session, discovery_role=True, access_method="web")
            return [
                {"domain": s.domain, "minutes": interval.minutes}
                for s in sources
                if (interval := cadence.check_interval(s)).origin == "override"
            ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read per-source intervals for the startup record (%s)", type(exc).__name__)
        return None


def _log_configuration(minutes: int) -> None:
    """003 FR-OBS-2: one structured record of the running configuration. Only numbers, names and domains:
    no secret, key or connection string is read into it."""
    logger.info(
        "scheduler configuration",
        extra={
            "tick_interval_minutes": minutes,
            "cadence_minutes": {
                "weekly": settings.cadence_weekly_minutes,
                "monthly": settings.cadence_monthly_minutes,
                "quarterly": settings.cadence_quarterly_minutes,
                "annual": settings.cadence_annual_minutes,
                "undeclared": settings.cadence_undeclared_minutes,
            },
            "source_overrides": _source_overrides(),
            "forced_full_recheck_days": settings.forced_full_recheck_days,
            "llm_generation_allowance": settings.llm_generation_daily_allowance,
            "llm_embedding_allowance": settings.llm_embedding_daily_allowance,
            "llm_user_facing_reserve": settings.llm_user_facing_reserve,
            "llm_ladder_offsets": {
                "backfill": settings.llm_ladder_offset_backfill,
                "monitoring": settings.llm_ladder_offset_monitoring,
                "validation": settings.llm_ladder_offset_validation,
            },
            "llm_backfill_daily_share": settings.llm_backfill_daily_share,
            "llm_budget_timezone": settings.llm_budget_timezone,
        },
    )


def start_scheduler(interval_minutes: int | None = None) -> BackgroundScheduler | None:
    """Idempotent: a second call while running returns the running scheduler."""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return _scheduler
        minutes = settings.monitoring_interval_minutes if interval_minutes is None else interval_minutes
        scheduler = build_scheduler(minutes)
        if scheduler is None:
            return None
        scheduler.start()
        _scheduler = scheduler
    _log_configuration(minutes)
    return scheduler


def stop_scheduler() -> None:
    """Idempotent: safe to call when nothing is running."""
    global _scheduler
    with _lock:
        scheduler, _scheduler = _scheduler, None
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
