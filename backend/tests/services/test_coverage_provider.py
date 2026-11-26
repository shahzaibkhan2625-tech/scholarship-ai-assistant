"""003 T288 (FR-ERR-2, R-10): the coverage failed-source count ignores provider-side failure rows."""

import uuid
from datetime import datetime, timedelta, timezone

from app.services.coverage import FetchLogSnapshot, SourceSnapshot, compute_coverage_summary

NOW = datetime(2026, 11, 26, tzinfo=timezone.utc)


def _source(source_type="gov") -> SourceSnapshot:
    return SourceSnapshot(id=uuid.uuid4(), country="Germany", source_type=source_type, status="active")


def _log(source_id, status, *, minutes=0, run=None, outcome=None) -> FetchLogSnapshot:
    return FetchLogSnapshot(
        source_id=source_id, status=status, started_at=NOW + timedelta(minutes=minutes), monitoring_run_id=run,
        outcome=outcome,
    )


def test_a_provider_failure_in_the_latest_run_is_not_a_failed_source():
    source, run = _source(), uuid.uuid4()
    logs = [_log(source.id, "ok", run=run), _log(source.id, "fail", minutes=1, run=run, outcome="provider_quota")]
    summary = compute_coverage_summary([source], logs, now=NOW)
    assert summary.sources_checked == 1 and summary.sources_failed == 0
    assert summary.gaps == []  # the source still counts as healthy, so no gap is reported


def test_a_provider_failure_on_the_latest_raw_row_is_not_a_failed_source():
    source = _source()
    summary = compute_coverage_summary([source], [_log(source.id, "fail", outcome="provider_error")], now=NOW)
    assert summary.sources_checked == 1 and summary.sources_failed == 0


def test_a_real_failure_is_still_counted():
    source, run = _source(), uuid.uuid4()
    logs = [_log(source.id, "ok", run=run), _log(source.id, "fail", minutes=1, run=run)]
    assert compute_coverage_summary([source], logs, now=NOW).sources_failed == 1


def test_a_real_failure_next_to_a_provider_failure_in_one_run_is_still_counted():
    source, run = _source(), uuid.uuid4()
    logs = [
        _log(source.id, "fail", run=run),
        _log(source.id, "fail", minutes=1, run=run, outcome="provider_parse"),
    ]
    assert compute_coverage_summary([source], logs, now=NOW).sources_failed == 1


def test_the_snapshot_outcome_defaults_to_none():
    assert _log(uuid.uuid4(), "ok").outcome is None
