"""002 US1 repository additions: source filters, touch_source_check,
mark_seen_verified, list_for_source, monitoring_repo. Real (rolled-back) DB."""

import inspect
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import monitoring_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringStatus, MonitoringTrigger
from app.models.scholarship import Scholarship
from app.models.source import ScholarshipSource
from app.schemas.source import SourceRegistryCreate


def _source(session, **overrides):
    data = dict(
        name="Repo Test", source_type="gov", official_status="official",
        domain=f"repo-{uuid.uuid4().hex[:10]}.example.com", access_method="web", reliability_level="high",
        status="active", discovery_role=True,
    )
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**{**data, **overrides}))


def test_get_active_sources_new_filters_are_keyword_only_and_default_to_no_filter():
    params = inspect.signature(source_repo.get_active_sources).parameters
    assert params["discovery_role"].kind is inspect.Parameter.KEYWORD_ONLY and params["discovery_role"].default is None
    assert params["access_method"].kind is inspect.Parameter.KEYWORD_ONLY and params["access_method"].default is None
    assert list(params)[:3] == ["db", "country", "source_type"]  # existing positional order untouched


def test_get_active_sources_filters_by_role_and_access_method(db_session_factory):
    session = db_session_factory()
    web_disc = _source(session)
    web_nodisc = _source(session, discovery_role=False)
    api_disc = _source(session, access_method="api")
    plain = {s.id for s in source_repo.get_active_sources(session)}
    assert {web_disc.id, web_nodisc.id, api_disc.id} <= plain  # no-arg behavior unchanged
    picked = {s.id for s in source_repo.get_active_sources(session, discovery_role=True, access_method="web")}
    assert web_disc.id in picked and web_nodisc.id not in picked and api_disc.id not in picked
    only_non_disc = {s.id for s in source_repo.get_active_sources(session, discovery_role=False)}
    assert web_nodisc.id in only_non_disc and web_disc.id not in only_non_disc
    session.close()


def test_touch_source_check_always_sets_checked_and_only_sets_success_on_success(db_session_factory):
    session = db_session_factory()
    source = _source(session)
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    touched = source_repo.touch_source_check(session, source.id, success=False, now=when)
    assert touched.last_checked_at == when and touched.last_success_at is None
    later = when + timedelta(hours=1)
    touched = source_repo.touch_source_check(session, source.id, success=True, now=later)
    assert touched.last_checked_at == later and touched.last_success_at == later
    again = source_repo.touch_source_check(session, source.id, success=False, now=later + timedelta(hours=1))
    assert again.last_success_at == later and again.status.value == "active"
    assert source_repo.touch_source_check(session, uuid.uuid4(), success=True) is None
    session.close()


def test_list_for_source_is_scoped_and_distinct_and_mark_seen_verified_touches_only_given_ids(db_session_factory):
    session = db_session_factory()
    mine, other = _source(session), _source(session)
    rows = []
    for source, label in ((mine, "a"), (mine, "b"), (other, "c")):
        s = Scholarship(name=f"Repo {label} {uuid.uuid4().hex[:6]}", official_scholarship_url="https://x.example.com")
        session.add(s)
        session.flush()
        session.add(ScholarshipSource(scholarship_id=s.id, source_id=source.id, url="https://x.example.com"))
        rows.append(s)
    session.add(ScholarshipSource(scholarship_id=rows[0].id, source_id=mine.id, url="https://dup.example.com"))
    session.commit()

    listed = scholarship_repo.list_for_source(session, mine.id)
    assert sorted(s.id for s in listed) == sorted([rows[0].id, rows[1].id])  # distinct, scoped

    now = datetime.now(timezone.utc)
    assert scholarship_repo.mark_seen_verified(session, [rows[0].id], now) == 1
    assert scholarship_repo.mark_seen_verified(session, [], now) == 0
    for s in rows:
        session.refresh(s)
    assert rows[0].last_verified_at == now and rows[1].last_verified_at is None and rows[2].last_verified_at is None
    session.close()


def test_list_runs_newest_first_and_limited(db_session_factory):
    session = db_session_factory()
    base = datetime.now(timezone.utc) + timedelta(days=1)
    ids = []
    for i in range(3):
        run = MonitoringRun(trigger=MonitoringTrigger.MANUAL, started_at=base + timedelta(seconds=i))
        session.add(run)
        session.flush()
        ids.append(run.id)
    session.commit()
    assert [r.id for r in monitoring_repo.list_runs(session, limit=3)] == list(reversed(ids))
    assert len(monitoring_repo.list_runs(session, limit=1)) == 1
    session.close()


def test_create_run_if_none_running_blocks_a_second_run(db_session_factory):
    session = db_session_factory()
    monitoring_repo.fail_leftover_running_runs(session)  # rolled back at teardown
    first = monitoring_repo.create_run_if_none_running(session, MonitoringTrigger.MANUAL)
    assert first is not None and first.status == MonitoringStatus.RUNNING
    assert monitoring_repo.create_run_if_none_running(session, MonitoringTrigger.MANUAL) is None
    monitoring_repo.finish_run(
        session, first.id, status=MonitoringStatus.COMPLETED, sources_processed=0, sources_failed=0, changes_detected=0
    )
    assert monitoring_repo.create_run_if_none_running(session, MonitoringTrigger.MANUAL) is not None
    session.close()


def test_concurrent_create_run_if_none_running_yields_exactly_one_run(db_session_factory):
    """Real, separate connections + real commits (so the transaction-scoped
    advisory lock is what serializes them); the rows are removed afterwards."""
    from app.data.repositories.db import SessionLocal

    with SessionLocal() as probe:
        if monitoring_repo.has_running_run(probe):
            pytest.skip("a real running monitoring run exists; not touching it")

    results: list = []
    barrier = threading.Barrier(4)

    def attempt():
        with SessionLocal() as s:
            barrier.wait()
            run = monitoring_repo.create_run_if_none_running(s, MonitoringTrigger.MANUAL)
            results.append(run.id if run else None)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len([r for r in results if r is not None]) == 1
    finally:
        with SessionLocal() as cleanup:
            for run_id in [r for r in results if r is not None]:
                row = cleanup.get(MonitoringRun, run_id)
                if row is not None:
                    cleanup.delete(row)
            cleanup.commit()
