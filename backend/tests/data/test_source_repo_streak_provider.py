"""003 T288 (FR-ERR-2/5, R-10): the shared failure-streak definition ignores provider-side runs.

A check that failed only because the LLM provider failed is an OK HTTP row plus a `FAIL` row whose `outcome`
starts with `provider_`. Such a run neither extends nor resets a streak. Retention keeps using the
unfiltered ranking, so its floors do not move. Real (rolled-back) DB."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.data.repositories import source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import FetchStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate

BASE = datetime(2027, 3, 1, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _source(session):
    return source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Streak", source_type="gov", official_status="official",
            domain=f"streak-{uuid.uuid4().hex[:8]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active",
        ),
    )


class Timeline:
    """Appends monitoring runs oldest to newest."""

    def __init__(self, session, source):
        self.session, self.source, self.minute = session, source, 0

    def _run(self):
        self.minute += 10
        run = MonitoringRun(trigger=MonitoringTrigger.SCHEDULED, started_at=BASE + timedelta(minutes=self.minute))
        self.session.add(run)
        self.session.commit()
        return run

    def _row(self, run, status, offset, **kw):
        return source_repo.log_fetch(
            self.session,
            SourceFetchLogCreate(
                source_id=self.source.id, status=status, monitoring_run_id=run.id,
                started_at=BASE + timedelta(minutes=self.minute, seconds=offset), **kw,
            ),
        )

    def ok(self):
        run = self._run()
        self._row(run, FetchStatus.OK, 0)
        self._row(run, FetchStatus.OK, 1, items_found=2)

    def site_failure(self):
        run = self._run()
        self._row(run, FetchStatus.OK, 0)
        self._row(run, FetchStatus.FAIL, 1, error="listing extraction yielded 0 candidates")

    def provider_failure(self, outcome="provider_quota"):
        run = self._run()
        self._row(run, FetchStatus.OK, 0)
        self._row(run, FetchStatus.FAIL, 1, outcome=outcome, error="provider quota")

    def deferred(self):
        run = self._run()
        self._row(run, FetchStatus.OK, 0)
        self._row(run, FetchStatus.OK, 1, outcome="deferred")


def _streak(session, source):
    return source_repo.consecutive_failure_count(session, source.id)


def test_a_provider_run_between_site_failures_neither_resets_nor_extends_the_streak(session):
    source = _source(session)
    t = Timeline(session, source)
    t.site_failure()
    t.provider_failure()
    t.site_failure()
    assert _streak(session, source) == 2


def test_provider_runs_at_the_head_do_not_extend_a_streak(session):
    source = _source(session)
    t = Timeline(session, source)
    for _ in range(3):
        t.site_failure()
    t.provider_failure("provider_error")
    t.provider_failure("provider_parse")
    assert _streak(session, source) == 3


def test_provider_runs_alone_leave_the_streak_at_zero(session):
    source = _source(session)
    t = Timeline(session, source)
    t.provider_failure()
    t.provider_failure()
    assert _streak(session, source) == 0


def test_a_real_site_failure_after_a_provider_failure_still_counts(session):
    source = _source(session)
    t = Timeline(session, source)
    t.ok()
    t.provider_failure()
    t.site_failure()
    assert _streak(session, source) == 1


def test_a_provider_run_does_not_hide_an_earlier_success_from_the_streak_end(session):
    source = _source(session)
    t = Timeline(session, source)
    t.site_failure()
    t.ok()
    t.provider_failure()
    t.site_failure()
    assert _streak(session, source) == 1  # the earlier success still ends the streak


def test_a_deferred_run_is_a_healthy_run(session):
    source = _source(session)
    t = Timeline(session, source)
    t.site_failure()
    t.deferred()
    assert _streak(session, source) == 0


def test_list_source_health_uses_the_same_definition(session):
    source = _source(session)
    t = Timeline(session, source)
    t.site_failure()
    t.provider_failure()
    rows = {s.id: n for s, n in source_repo.list_source_health(session)}
    assert rows[source.id] == 1


def test_retention_ranking_still_counts_provider_runs(session):
    """Retention floors are unchanged: the unfiltered ranking keeps every run, the streak one skips provider runs."""
    source = _source(session)
    t = Timeline(session, source)
    t.site_failure()
    t.provider_failure()
    t.ok()

    def runs(**kwargs):
        ranked = source_repo.ranked_monitoring_runs([source.id], **kwargs)
        return session.execute(select(ranked.c.monitoring_run_id).where(ranked.c.source_id == source.id)).all()

    assert len(runs()) == 3
    assert len(runs(exclude_provider=True)) == 2


def test_provider_failure_stats_are_reported_separately(session):
    source = _source(session)
    other = _source(session)
    t = Timeline(session, source)
    t.site_failure()
    t.provider_failure()
    t.ok()
    t.provider_failure("provider_error")
    stats = source_repo.provider_failure_stats(session, [source.id, other.id])
    assert stats[source.id].count == 2
    assert stats[source.id].last_at is not None and stats[source.id].last_at > BASE + timedelta(minutes=30)
    assert other.id not in stats


def test_provider_failure_count_covers_only_the_last_window_of_runs(session):
    source = _source(session)
    t = Timeline(session, source)
    t.provider_failure()
    for _ in range(3):
        t.ok()
    stats = source_repo.provider_failure_stats(session, [source.id], window=3)
    assert stats[source.id].count == 0
    assert stats[source.id].last_at is not None  # the last failure is still reported, just not "recent"
