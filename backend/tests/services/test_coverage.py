"""`coverage` service tests (T084, Blueprint §30.4): checked/failed counts are
derived correctly from a mix of active/failing/disabled sources and ok/fail
fetch-log entries, and the summary never claims complete coverage — a gap is
always reported for any (country, source_type) combination with zero active,
currently-healthy sources. Pure unit tests (no DB): `compute_coverage_summary`
is a plain function over fixture dataclasses.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.services.coverage import FetchLogSnapshot, SourceSnapshot, compute_coverage_summary

_NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def _source(status="active", country="Germany", source_type="gov") -> SourceSnapshot:
    return SourceSnapshot(id=uuid.uuid4(), country=country, source_type=source_type, status=status)


def _log(source_id, status, *, when=_NOW, run=None) -> FetchLogSnapshot:
    return FetchLogSnapshot(source_id=source_id, status=status, started_at=when, monitoring_run_id=run)


def test_never_claims_complete_coverage_by_construction():
    summary = compute_coverage_summary([], [], now=_NOW)
    # The schema itself pins this to Literal[False] — this assertion documents
    # that the service never even tries to set it otherwise.
    assert summary.claims_complete_coverage is False


def test_counts_configured_active_checked_failed_correctly():
    ok_source = _source(status="active", country="Germany", source_type="gov")
    failing_source = _source(status="active", country="Germany", source_type="university")
    disabled_source = _source(status="disabled", country="Pakistan", source_type="gov")
    unchecked_active_source = _source(status="active", country="Pakistan", source_type="university")

    sources = [ok_source, failing_source, disabled_source, unchecked_active_source]
    logs = [
        _log(ok_source.id, "ok"),
        _log(failing_source.id, "fail"),
        _log(disabled_source.id, "fail"),
    ]

    summary = compute_coverage_summary(sources, logs, now=_NOW)

    assert summary.sources_configured == 4
    assert summary.sources_active == 3  # ok_source, failing_source, unchecked_active_source
    assert summary.sources_checked == 3  # ok_source, failing_source, disabled_source all have a log
    assert summary.sources_failed == 2  # failing_source + disabled_source's latest log is 'fail'
    assert str(ok_source.id) in summary.last_checked_at


def test_gap_reported_when_no_active_source_for_a_combo():
    # Only source for (France, gov) is disabled -> that combo is a gap.
    disabled_only = _source(status="disabled", country="France", source_type="gov")

    summary = compute_coverage_summary([disabled_only], [], now=_NOW)

    assert any("France" in gap and "gov" in gap for gap in summary.gaps)
    assert "France" not in summary.countries_covered


def test_gap_reported_when_every_active_source_for_a_combo_is_currently_failing():
    active_but_failing = _source(status="active", country="Norway", source_type="gov")
    log = _log(active_but_failing.id, "fail")

    summary = compute_coverage_summary([active_but_failing], [log], now=_NOW)

    assert any("Norway" in gap for gap in summary.gaps)
    assert "Norway" not in summary.countries_covered


def test_no_gap_when_an_active_healthy_source_covers_the_combo():
    healthy = _source(status="active", country="Hungary", source_type="gov")
    log = _log(healthy.id, "ok")

    summary = compute_coverage_summary([healthy], [log], now=_NOW)

    assert not any("Hungary" in gap for gap in summary.gaps)
    assert "Hungary" in summary.countries_covered


def test_latest_log_wins_over_an_earlier_stale_failure():
    source = _source(status="active", country="Sweden", source_type="gov")
    logs = [
        _log(source.id, "fail", when=_NOW - timedelta(days=5)),
        _log(source.id, "ok", when=_NOW),
    ]

    summary = compute_coverage_summary([source], logs, now=_NOW)

    assert summary.sources_failed == 0
    assert "Sweden" in summary.countries_covered


def test_never_emits_zero_gaps_when_a_combo_has_no_active_source_at_all_even_if_others_are_healthy():
    healthy = _source(status="active", country="Hungary", source_type="gov")
    healthy_log = _log(healthy.id, "ok")
    uncovered = _source(status="disabled", country="Morocco", source_type="gov")

    summary = compute_coverage_summary([healthy, uncovered], [healthy_log], now=_NOW)

    assert summary.gaps != []
    assert any("Morocco" in gap for gap in summary.gaps)


# --- 002 US4 (T205): FR-COVAN-1..4 --------------------------------------------------


def test_new_counts_default_to_zero_so_001_callers_are_unchanged():
    summary = compute_coverage_summary([], [], now=_NOW)
    assert summary.candidate_sources_pending == 0
    assert summary.verified_opportunities_count == 0


def test_pending_candidates_and_verified_count_are_passed_through_and_separate_from_active():
    active = _source(status="active")
    summary = compute_coverage_summary(
        [active], [], now=_NOW, candidate_sources_pending=3, verified_opportunities_count=7
    )
    assert summary.candidate_sources_pending == 3
    assert summary.verified_opportunities_count == 7
    # Candidates are never folded into the registry counts (FR-COVAN-3).
    assert summary.sources_configured == 1
    assert summary.sources_active == 1


def test_failing_status_counts_as_active_but_unhealthy_with_its_own_gap_reason():
    failing = _source(status="failing", country="Norway", source_type="gov")
    healthy_elsewhere = _source(status="active", country="Hungary", source_type="gov")

    summary = compute_coverage_summary(
        [failing, healthy_elsewhere], [_log(failing.id, "ok"), _log(healthy_elsewhere.id, "ok")], now=_NOW
    )

    assert summary.sources_active == 2
    norway = [gap for gap in summary.gaps if "Norway" in gap]
    assert len(norway) == 1
    assert "no active source configured" not in norway[0]
    assert "failing" in norway[0]
    assert "Norway" not in summary.countries_covered


def test_a_combo_with_no_active_or_failing_source_keeps_the_no_active_source_reason():
    disabled = _source(status="disabled", country="France", source_type="gov")
    summary = compute_coverage_summary([disabled], [], now=_NOW)
    assert any("France" in gap and "no active source configured" in gap for gap in summary.gaps)


def test_failed_is_derived_from_the_latest_monitoring_run_not_the_latest_row():
    """A monitored check is an OK HTTP row + a FAIL extraction row in one run (ADR-0008)."""
    run_old, run_new = uuid.uuid4(), uuid.uuid4()
    source = _source(status="active", country="Sweden")
    logs = [
        _log(source.id, "ok", when=_NOW - timedelta(days=2), run=run_old),
        _log(source.id, "ok", when=_NOW - timedelta(minutes=2), run=run_new),
        _log(source.id, "fail", when=_NOW - timedelta(minutes=1), run=run_new),
    ]
    summary = compute_coverage_summary([source], logs, now=_NOW)
    assert summary.sources_failed == 1
    assert "Sweden" not in summary.countries_covered


def test_a_later_ingestion_stage_failure_without_a_run_does_not_fail_a_healthy_run():
    run = uuid.uuid4()
    source = _source(status="active", country="Sweden")
    logs = [
        _log(source.id, "ok", when=_NOW - timedelta(minutes=5), run=run),
        _log(source.id, "fail", when=_NOW, run=None),  # per-scholarship ingestion row
    ]
    summary = compute_coverage_summary([source], logs, now=_NOW)
    assert summary.sources_failed == 0
    assert "Sweden" in summary.countries_covered
    # ...but last_checked_at still reflects the most recent row of any kind.
    assert summary.last_checked_at[str(source.id)] == _NOW


def test_a_successful_latest_run_recovers_a_source_whose_earlier_run_failed():
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    source = _source(status="active")
    logs = [
        _log(source.id, "fail", when=_NOW - timedelta(days=1), run=r1),
        _log(source.id, "ok", when=_NOW, run=r2),
    ]
    assert compute_coverage_summary([source], logs, now=_NOW).sources_failed == 0


def test_without_any_monitoring_run_the_latest_raw_row_decides():
    source = _source(status="active")
    logs = [_log(source.id, "ok", when=_NOW - timedelta(days=1)), _log(source.id, "fail", when=_NOW)]
    assert compute_coverage_summary([source], logs, now=_NOW).sources_failed == 1


def test_last_checked_at_is_the_most_recent_row_per_source_not_a_stale_one():
    a, b = _source(), _source()
    logs = [
        _log(a.id, "ok", when=_NOW - timedelta(days=9)),
        _log(a.id, "ok", when=_NOW - timedelta(hours=1)),
        _log(b.id, "ok", when=_NOW - timedelta(days=3)),
    ]
    summary = compute_coverage_summary([a, b], logs, now=_NOW)
    assert summary.last_checked_at[str(a.id)] == _NOW - timedelta(hours=1)
    assert summary.last_checked_at[str(b.id)] == _NOW - timedelta(days=3)
