"""T182: scheduler registration/idempotency. No real tick ever fires: jobs are
only registered on an unstarted scheduler, or the scheduler is started with a
far-future interval and immediately stopped."""

from app.scheduling import scheduler as sched_mod
from app.scheduling.scheduler import JOB_ID, build_scheduler, start_scheduler, stop_scheduler


def test_build_scheduler_disabled_for_non_positive_interval():
    assert build_scheduler(0) is None
    assert build_scheduler(-5) is None


def test_build_scheduler_registers_single_guarded_interval_job():
    scheduler = build_scheduler(15)
    assert scheduler is not None and not scheduler.running
    [job] = scheduler.get_jobs()
    assert job.id == JOB_ID
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.trigger.interval.total_seconds() == 15 * 60
    assert job.func is sched_mod._run_scheduled_monitor


def test_scheduled_job_runs_monitor_with_scheduled_trigger(monkeypatch):
    from app.models.monitoring import MonitoringTrigger
    import app.workflows.source_monitor.graph as graph_mod

    calls = []
    monkeypatch.setattr(graph_mod, "run_source_monitor", lambda session, *, trigger, **kw: calls.append(trigger))
    # Call directly with a stub session factory so no DB is touched.
    import app.data.repositories.db as db_mod

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _Session())
    sched_mod._run_scheduled_monitor()
    assert calls == [MonitoringTrigger.SCHEDULED]


def test_start_and_stop_are_idempotent():
    try:
        assert start_scheduler(0) is None  # disabled: nothing started
        first = start_scheduler(24 * 60)
        second = start_scheduler(24 * 60)
        assert first is not None and first is second and first.running
        stop_scheduler()
        stop_scheduler()  # second stop is a safe no-op
        assert sched_mod._scheduler is None
    finally:
        stop_scheduler()
