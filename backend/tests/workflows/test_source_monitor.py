"""`source_monitor` workflow tests (002 US1, T170/T171/T179/T180/T183).

Real (rolled-back) DB; every network/LLM/scheduler seam is mocked: `fetch`
and `extract_listing` are injected, ingestion's classifier is patched, no
scheduler tick ever fires. `get_active_sources` is wrapped to scope the run to
the sources THIS test created (the shared DB may hold real seeded sources) —
while still recording the filter kwargs the workflow passed.
"""

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from app.data.repositories import monitoring_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringStatus, MonitoringTrigger
from app.models.scholarship import (
    DegreeLevel,
    FundingStatus,
    LifecycleStatus,
    Scholarship,
    ScholarshipField,
    VerificationStatus,
)
from app.models.source import FetchStatus, SourceStatus
from app.scheduling.locks import try_acquire_source_lock
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor import graph as monitor_graph
from app.workflows.source_monitor.graph import run_source_monitor

_FAKE_CLASSIFICATION = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred",
    funding_status="fully_funded", funding_status_confidence="inferred",
    provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred",
    field="Computer Science", field_confidence="inferred",
)


class _Listing:
    """Scripted `extract_listing`: candidates per source host."""

    def __init__(self):
        self.by_host: dict[str, list[ExtractedListingCandidate]] = {}
        self.raise_for: set[str] = set()

    def __call__(self, html, *, source_url):
        host = urlparse(source_url).hostname
        if host in self.raise_for:
            raise RuntimeError("extractor exploded")
        candidates = self.by_host.get(host, [])
        return ExtractListingOutput(candidates=candidates, accepted_count=len(candidates), rejected_count=0)


class _Fetcher:
    def __init__(self):
        self.failing_hosts: set[str] = set()
        self.calls: list[str] = []
        self.on_call = None

    def __call__(self, url):
        self.calls.append(url)
        if self.on_call:
            self.on_call(url)
        if urlparse(url).hostname in self.failing_hosts:
            return FetchResult(url=url, success=False, status_code=503, error="Server responded with HTTP 503")
        return FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>")


def _cand(name, **overrides):
    base = dict(name=name, degree_level="PhD", funding_status="fully_funded", deadline="2027-03-01")
    return ExtractedListingCandidate(**{**base, **overrides})


@pytest.fixture
def env(db_session_factory, monkeypatch):
    session = db_session_factory()
    scoped_ids: set[uuid.UUID] = set()
    filter_calls: list[dict] = []
    real = source_repo.get_active_sources

    def scoped(db, *args, **kwargs):
        filter_calls.append(kwargs)
        return [s for s in real(db, *args, **kwargs) if s.id in scoped_ids]

    monkeypatch.setattr(source_repo, "get_active_sources", scoped)
    monkeypatch.setattr(
        "app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: _FAKE_CLASSIFICATION
    )

    class Env:
        pass

    e = Env()
    e.session, e.filter_calls = session, filter_calls
    e.listing, e.fetcher = _Listing(), _Fetcher()
    e.uid = uuid.uuid4().hex[:8]

    def make_source(label="s", **overrides):
        data = dict(
            name=f"Monitor Test {label}", source_type="gov", official_status="official",
            domain=f"monitor-{label}-{e.uid}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active",
        )
        source = source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**{**data, **overrides}))
        scoped_ids.add(source.id)
        return source

    def run():
        return run_source_monitor(
            session, trigger=MonitoringTrigger.MANUAL, fetch=e.fetcher, extract_listing=e.listing, sleep=lambda _d: None
        )

    def stored(source):
        return {s.name: s for s in scholarship_repo.list_for_source(session, source.id)}

    e.make_source, e.run, e.stored = make_source, run, stored
    try:
        yield e
    finally:
        session.close()


def _name(env, label):
    return f"{label} Scholarship {env.uid}"


# --- new / unchanged / changed -------------------------------------------------


def test_new_listing_is_ingested_unconfirmed_and_run_is_recorded(env):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]

    before = datetime.now(timezone.utc)
    run = env.run()

    assert run.status == MonitoringStatus.COMPLETED and run.ended_at is not None
    assert (run.sources_processed, run.sources_failed, run.changes_detected) == (1, 0, 1)
    created = env.stored(source)[_name(env, "Alpha")]
    assert created.lifecycle_status == LifecycleStatus.NEWLY_DISCOVERED  # born as today's discovery makes it
    assert created.verification_status == VerificationStatus.UNVERIFIED
    assert created.last_verified_at is not None and created.last_verified_at >= before  # D4 applies to new too
    # every fetch-log row of this fetch carries the run id
    logs = monitoring_repo.get_fetch_logs_for_run(env.session, run.id)
    assert logs and all(log.monitoring_run_id == run.id for log in logs)
    assert any(log.status == FetchStatus.OK and log.items_found == 1 for log in logs)
    assert source_repo.get_source_by_id(env.session, source.id).last_success_at is not None


def test_unchanged_listing_makes_no_ingestion_call_and_no_spurious_rows(env, monkeypatch):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    scholarship = env.stored(source)[_name(env, "Alpha")]
    field_rows_before = len(scholarship_repo.get_field_rows(env.session, scholarship.id))

    calls = []
    real = monitor_graph.run_ingestion
    monkeypatch.setattr(monitor_graph, "run_ingestion", lambda *a, **k: calls.append(k) or real(*a, **k))
    run = env.run()

    assert calls == []
    assert run.changes_detected == 0 and run.sources_processed == 1
    assert len(scholarship_repo.get_field_rows(env.session, scholarship.id)) == field_rows_before
    assert not [r for r in scholarship_repo.get_field_rows(env.session, scholarship.id) if r.value_status == "conflicting"]


def test_changed_deadline_overwrites_with_prior_value_audited_even_over_verified_data(env):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    scholarship = env.stored(source)[_name(env, "Alpha")]
    scholarship.verification_status = VerificationStatus.VERIFIED
    scholarship.lifecycle_status = LifecycleStatus.VERIFIED
    env.session.commit()

    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"), deadline="2027-09-15")]
    run = env.run()

    env.session.refresh(scholarship)
    assert run.changes_detected == 1
    assert scholarship.deadline.isoformat() == "2027-09-15"  # unconfirmed listing value wins (policy)
    assert scholarship.lifecycle_status == LifecycleStatus.UPDATED
    prior = [r for r in scholarship_repo.get_field_rows(env.session, scholarship.id) if r.key == "_prior:deadline"]
    assert len(prior) == 1
    assert prior[0].value["value"] == "2027-03-01" and prior[0].value["replaced_at"]
    assert prior[0].value_status == "known" and prior[0].confidence == "verified"
    assert prior[0].source_id == source.id
    # no conflict machinery was involved, and no second scholarships row exists
    assert not [r for r in scholarship_repo.get_field_rows(env.session, scholarship.id) if r.value_status == "conflicting"]
    assert len(env.stored(source)) == 1


def test_changed_funding_and_degree_are_detected_too(env):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"), funding_status="tuition_only", degree_level="MS")]
    env.run()
    scholarship = env.stored(source)[_name(env, "Alpha")]
    assert scholarship.funding_status == FundingStatus.TUITION_ONLY and scholarship.degree_level == DegreeLevel.MS
    keys = {r.key for r in scholarship_repo.get_field_rows(env.session, scholarship.id)}
    assert {"_prior:funding_status", "_prior:degree_level"} <= keys


def test_silent_listing_never_degrades_known_data_to_unknown(env):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"), funding_status="unknown", deadline=None)]
    run = env.run()
    scholarship = env.stored(source)[_name(env, "Alpha")]
    assert run.changes_detected == 0
    assert scholarship.funding_status == FundingStatus.FULLY_FUNDED and scholarship.deadline.isoformat() == "2027-03-01"


# --- absence / closed / reopened ------------------------------------------------


def _two_runs_setup(env, **source_overrides):
    source = env.make_source(**source_overrides)
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha")), _cand(_name(env, "Beta"))]
    env.run()
    return source


def test_absence_closes_only_when_listing_complete_and_shrink_guard_passes(env):
    source = _two_runs_setup(env, extraction_rules={"listing_complete": True})
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]  # 1 >= 0.5 * 2
    run = env.run()
    stored = env.stored(source)
    assert stored[_name(env, "Beta")].lifecycle_status == LifecycleStatus.CLOSED
    assert stored[_name(env, "Alpha")].lifecycle_status != LifecycleStatus.CLOSED
    assert run.changes_detected == 1


def test_absence_never_closes_without_listing_complete(env):
    source = _two_runs_setup(env)  # no listing_complete flag (every current seed)
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    before = env.stored(source)[_name(env, "Beta")].lifecycle_status
    env.run()
    assert env.stored(source)[_name(env, "Beta")].lifecycle_status == before


def test_absence_never_closes_when_shrink_guard_fails(env):
    source = env.make_source(extraction_rules={"listing_complete": True})
    names = [_name(env, n) for n in ("A", "B", "C", "D")]
    env.listing.by_host[source.domain] = [_cand(n) for n in names]
    env.run()
    env.listing.by_host[source.domain] = [_cand(names[0])]  # 1 < 0.5 * 4
    env.run()
    assert all(s.lifecycle_status != LifecycleStatus.CLOSED for s in env.stored(source).values())


def test_absence_never_closes_without_a_baseline_run(env):
    source = env.make_source(extraction_rules={"listing_complete": True})
    # stored by a prior (discovery-style) ingestion: no earlier monitoring fetch log for this URL
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    # wipe the baseline: pretend there was no previous successful extraction log
    for log in source_repo.get_fetch_logs_for_source(env.session, source.id):
        env.session.delete(log)
    env.session.commit()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Other"))]
    env.run()
    assert env.stored(source)[_name(env, "Alpha")].lifecycle_status != LifecycleStatus.CLOSED


def test_explicit_closed_signal_closes_outside_the_completeness_gate(env):
    class _ClosedCandidate(ExtractedListingCandidate):
        closing_status: str | None = None

    source = env.make_source()  # no listing_complete
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    env.listing.by_host[source.domain] = [
        _ClosedCandidate(name=_name(env, "Alpha"), degree_level="PhD", funding_status="fully_funded",
                         deadline="2027-03-01", closing_status="closed")
    ]
    env.run()
    assert env.stored(source)[_name(env, "Alpha")].lifecycle_status == LifecycleStatus.CLOSED


def test_closed_record_listed_again_becomes_reopened(env):
    source = _two_runs_setup(env, extraction_rules={"listing_complete": True})
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    assert env.stored(source)[_name(env, "Beta")].lifecycle_status == LifecycleStatus.CLOSED
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha")), _cand(_name(env, "Beta"))]
    run = env.run()
    assert env.stored(source)[_name(env, "Beta")].lifecycle_status == LifecycleStatus.REOPENED
    assert run.changes_detected == 1


# --- stale sweep / freshness (D4) ------------------------------------------------


def test_seen_record_is_refreshed_before_sweep_while_unseen_old_record_goes_stale(env):
    source = _two_runs_setup(env, freshness_window_days=30)
    old = datetime.now(timezone.utc) - timedelta(days=100)
    for s in env.stored(source).values():
        s.lifecycle_status, s.last_verified_at = LifecycleStatus.UNVERIFIED, old
    env.session.commit()

    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]  # Beta absent; gate not met -> not closed
    env.run()

    stored = env.stored(source)
    alpha, beta = stored[_name(env, "Alpha")], stored[_name(env, "Beta")]
    assert alpha.last_verified_at > old and alpha.lifecycle_status != LifecycleStatus.STALE  # never stale the run it was seen
    assert beta.lifecycle_status == LifecycleStatus.STALE
    assert beta.last_verified_at == old


def test_sweep_is_scoped_to_the_monitored_sources_scholarships(env):
    source = env.make_source(freshness_window_days=30)
    other = env.make_source("other", freshness_window_days=30)
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.listing.by_host[other.domain] = [_cand(_name(env, "Gamma"))]
    env.run()
    gamma = env.stored(other)[_name(env, "Gamma")]
    gamma.lifecycle_status, gamma.last_verified_at = (
        LifecycleStatus.UNVERIFIED, datetime.now(timezone.utc) - timedelta(days=100),
    )
    env.session.commit()
    env.fetcher.failing_hosts.add(other.domain)  # other fails; sweep runs only for what it owns anyway
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    alpha = env.stored(source)[_name(env, "Alpha")]
    assert alpha.lifecycle_status != LifecycleStatus.STALE


# --- failures (D3 / D5) ----------------------------------------------------------


def test_failed_fetch_keeps_source_active_marks_unavailable_and_recovers_next_run(env):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    scholarship = env.stored(source)[_name(env, "Alpha")]
    scholarship.lifecycle_status = LifecycleStatus.UNVERIFIED
    env.session.commit()

    env.fetcher.failing_hosts.add(source.domain)
    run = env.run()
    assert (run.sources_processed, run.sources_failed) == (1, 1)
    refreshed = source_repo.get_source_by_id(env.session, source.id)
    assert refreshed is not None and refreshed.status == SourceStatus.ACTIVE  # D5: not marked failing
    assert refreshed.last_checked_at is not None
    env.session.refresh(scholarship)
    assert scholarship.lifecycle_status == LifecycleStatus.SOURCE_UNAVAILABLE
    fail_logs = [log for log in monitoring_repo.get_fetch_logs_for_run(env.session, run.id) if log.status == FetchStatus.FAIL]
    assert fail_logs

    env.fetcher.failing_hosts.clear()  # retried on the next run regardless of the last outcome
    env.run()
    env.session.refresh(scholarship)
    assert scholarship.lifecycle_status == LifecycleStatus.UNVERIFIED


def test_empty_extraction_fails_the_source_run_but_touches_no_records_and_keeps_source_active(env):
    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    env.run()
    env.listing.by_host[source.domain] = []
    run = env.run()
    scholarship = env.stored(source)[_name(env, "Alpha")]
    assert run.sources_failed == 1
    assert scholarship.lifecycle_status != LifecycleStatus.CLOSED
    assert source_repo.get_source_by_id(env.session, source.id).status == SourceStatus.ACTIVE


def test_one_source_exception_is_counted_and_does_not_stop_the_rest(env):
    bad = env.make_source("bad")
    good = env.make_source("good")
    env.listing.raise_for.add(bad.domain)
    env.listing.by_host[good.domain] = [_cand(_name(env, "Alpha"))]
    run = env.run()
    assert run.status == MonitoringStatus.COMPLETED
    assert (run.sources_processed, run.sources_failed) == (2, 1)
    assert _name(env, "Alpha") in env.stored(good)


def test_counters_for_mixed_outcomes(env):
    ok = env.make_source("ok")
    down = env.make_source("down")
    env.listing.by_host[ok.domain] = [_cand(_name(env, "Alpha"))]
    env.fetcher.failing_hosts.add(down.domain)
    run = env.run()
    assert (run.sources_processed, run.sources_failed, run.changes_detected) == (2, 1, 1)
    assert source_repo.get_source_by_id(env.session, down.id).last_success_at is None


# --- selection / skipping --------------------------------------------------------


def test_source_selection_filter_and_exclusions(env):
    included = env.make_source("in")
    excluded = env.make_source("nodisc", discovery_role=False)
    api_source = env.make_source("api", access_method="api")
    env.listing.by_host[included.domain] = [_cand(_name(env, "Alpha"))]
    run = env.run()
    assert {"discovery_role": True, "access_method": "web"} in env.filter_calls
    fetched_hosts = {urlparse(u).hostname for u in env.fetcher.calls}
    assert fetched_hosts == {included.domain}
    assert excluded.domain not in fetched_hosts and api_source.domain not in fetched_hosts
    assert run.sources_processed == 1


def test_source_disabled_mid_run_is_skipped_not_failed_not_processed(env):
    first = env.make_source("a")
    second = env.make_source("b")
    env.listing.by_host[first.domain] = [_cand(_name(env, "Alpha"))]
    env.listing.by_host[second.domain] = [_cand(_name(env, "Beta"))]
    order: list[str] = []

    def disable_the_other_one(url):
        host = urlparse(url).hostname
        order.append(host)
        if len(order) == 1:  # while the first source is being fetched, disable the one still queued
            other = second if host == first.domain else first
            row = env.session.get(type(other), other.id)
            row.status = SourceStatus.DISABLED
            env.session.commit()

    env.fetcher.on_call = disable_the_other_one
    run = env.run()
    assert len(order) == 1  # the disabled source was never fetched
    assert (run.sources_processed, run.sources_failed) == (1, 0)


def test_source_whose_lock_is_held_elsewhere_is_skipped(env):
    from app.data.repositories.db import engine

    source = env.make_source()
    env.listing.by_host[source.domain] = [_cand(_name(env, "Alpha"))]
    with engine.connect() as holder:
        txn = holder.begin()
        assert try_acquire_source_lock(holder, source.id) is True
        run = env.run()
        txn.rollback()
    assert env.fetcher.calls == []
    assert (run.sources_processed, run.sources_failed) == (0, 0)
    assert env.run().sources_processed == 1  # free again once the holder's transaction ended


# --- run lifecycle ---------------------------------------------------------------


def test_ended_at_is_set_exactly_once(env):
    env.make_source()
    run = env.run()
    ended = run.ended_at
    assert ended is not None
    assert monitoring_repo.finish_run(
        env.session, run.id, status=MonitoringStatus.FAILED, sources_processed=99, sources_failed=99, changes_detected=99
    ) is False
    env.session.refresh(run)
    assert run.ended_at == ended and run.status == MonitoringStatus.COMPLETED and run.sources_processed != 99


def test_exception_outside_the_source_loop_marks_the_run_failed(env, monkeypatch):
    def boom(db, *args, **kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(source_repo, "get_active_sources", boom)
    with pytest.raises(RuntimeError):
        run_source_monitor(env.session, trigger=MonitoringTrigger.SCHEDULED, fetch=env.fetcher, extract_listing=env.listing)
    runs = [r for r in monitoring_repo.list_runs(env.session, limit=5) if r.trigger == MonitoringTrigger.SCHEDULED]
    failed = runs[0]
    assert failed.status == MonitoringStatus.FAILED and failed.ended_at is not None


def test_unknown_run_id_is_rejected(env):
    with pytest.raises(ValueError):
        run_source_monitor(env.session, trigger=MonitoringTrigger.MANUAL, run_id=uuid.uuid4())


def test_crash_recovery_fails_only_leftover_running_runs(env):
    stuck = monitoring_repo.create_run(env.session, MonitoringTrigger.SCHEDULED)
    done = monitoring_repo.create_run(env.session, MonitoringTrigger.MANUAL)
    monitoring_repo.finish_run(
        env.session, done.id, status=MonitoringStatus.COMPLETED, sources_processed=0, sources_failed=0, changes_detected=0
    )
    assert monitoring_repo.fail_leftover_running_runs(env.session) >= 1
    env.session.refresh(stuck)
    env.session.refresh(done)
    assert stuck.status == MonitoringStatus.FAILED and stuck.ended_at is not None
    assert done.status == MonitoringStatus.COMPLETED
    assert env.session.execute(select(Scholarship.id).limit(0)).all() == []  # session still healthy
