"""Periodic source-monitoring scheduler (002 FR-MON-1).

APScheduler 3.x `BackgroundScheduler`: one interval job, `max_instances=1` and
`coalesce=True` so a slow run never overlaps itself and missed ticks collapse
into one. Each tick runs `run_source_monitor(trigger=SCHEDULED)` on its own
`SessionLocal()` session. `MONITORING_INTERVAL_MINUTES <= 0` disables it.

T230: a second job prunes `source_fetch_log` daily (03:00 UTC, same guards). It is
coupled to the monitoring switch: no scheduler, no retention (no extra setting).
"""

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.models.monitoring import MonitoringTrigger

logger = logging.getLogger(__name__)

__all__ = ["JOB_ID", "RETENTION_JOB_ID", "build_scheduler", "start_scheduler", "stop_scheduler"]

JOB_ID = "source_monitor"
RETENTION_JOB_ID = "fetch_log_retention"

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()


def _run_scheduled_monitor() -> None:
    from app.data.repositories.db import SessionLocal
    from app.workflows.source_monitor.graph import run_source_monitor

    with SessionLocal() as session:
        try:
            run_source_monitor(session, trigger=MonitoringTrigger.SCHEDULED)
        except Exception:  # noqa: BLE001 - a failed tick must never kill the scheduler thread
            logger.exception("scheduled source monitoring run failed")


def _run_scheduled_retention() -> None:
    from app.data.repositories.db import SessionLocal
    from app.services import retention

    with SessionLocal() as session:
        try:
            retention.prune_fetch_log(session)  # settings defaults, every source
        except Exception:  # noqa: BLE001 - a failed tick (e.g. Neon cold start) retries next day
            logger.exception("scheduled fetch-log retention failed")


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
        return scheduler


def stop_scheduler() -> None:
    """Idempotent: safe to call when nothing is running."""
    global _scheduler
    with _lock:
        scheduler, _scheduler = _scheduler, None
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
