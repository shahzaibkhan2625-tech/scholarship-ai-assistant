"""`allow_failing` gate widening + monitor-only lookups + health repo helpers
(002 US3 T198/T200/T204, ADR-0008). Principle IV: only ACTIVE (default) or
ACTIVE|FAILING (monitor recovery) sources are fetchable; PENDING and DISABLED
never are. The 001 discovery path keeps marking a source failing on its first
failed fetch — unchanged, and asserted here."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import FetchStatus, SourceStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.sources.connectors.official_fetch import fetch_and_extract_listing, fetch_official
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _source(session, status, label="g", **overrides):
    data = dict(
        name=f"Gate Test {label}", source_type="gov", official_status="official",
        domain=f"gate-{label}-{uuid.uuid4().hex[:8]}.example.com", access_method="web",
        discovery_role=True, reliability_level="high", status=status,
    )
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**{**data, **overrides}))


def _ok_fetch(url):
    return FetchResult(url=url, success=True, status_code=200, html="<html>x</html>")


def _bad_fetch(url):
    return FetchResult(url=url, success=False, status_code=503, error="HTTP 503")


def _extract(html, *, source_url):
    cand = ExtractedListingCandidate(name="Gate Scholarship", degree_level="PhD", funding_status="fully_funded")
    return ExtractListingOutput(candidates=[cand], accepted_count=1, rejected_count=0)


# --- connector gate ------------------------------------------------------------------


def test_default_gate_is_unchanged_failing_source_is_rejected(session):
    source = _source(session, "failing")
    with pytest.raises(source_repo.SourceNotApprovedError):
        fetch_official(session, source.id, f"https://{source.domain}", fetch=_ok_fetch, sleep=lambda _d: None)
    with pytest.raises(source_repo.SourceNotApprovedError):
        fetch_and_extract_listing(session, source.id, None, fetch=_ok_fetch, extract_listing=_extract)


def test_allow_failing_admits_a_failing_source_in_both_connector_entry_points(session):
    source = _source(session, "failing")
    result = fetch_official(
        session, source.id, f"https://{source.domain}", fetch=_ok_fetch, sleep=lambda _d: None, allow_failing=True
    )
    assert result.success is True
    listing = fetch_and_extract_listing(
        session, source.id, None, fetch=_ok_fetch, extract_listing=_extract, bypass_cache=True, allow_failing=True
    )
    assert listing.status == "ok" and listing.accepted_count == 1


@pytest.mark.parametrize("status", ["pending", "disabled"])
def test_allow_failing_never_admits_pending_or_disabled(session, status):
    source = _source(session, status)
    with pytest.raises(source_repo.SourceNotApprovedError):
        fetch_official(
            session, source.id, f"https://{source.domain}", fetch=_ok_fetch, sleep=lambda _d: None, allow_failing=True
        )
    with pytest.raises(source_repo.SourceNotApprovedError):
        fetch_and_extract_listing(session, source.id, None, fetch=_ok_fetch, extract_listing=_extract, allow_failing=True)


def test_discovery_default_still_marks_failing_on_the_first_failed_fetch(session):
    """ADR-0008 / Q4: the 001 rule is intentionally different from monitoring's N-run rule."""
    source = _source(session, "active")
    result = fetch_official(session, source.id, f"https://{source.domain}", fetch=_bad_fetch, sleep=lambda _d: None)
    assert result.success is False
    session.refresh(source)
    assert source.status == SourceStatus.FAILING


# --- monitor-only lookups ----------------------------------------------------------------


def test_monitorable_lookups_include_active_and_failing_only(session):
    active, failing = _source(session, "active", "a"), _source(session, "failing", "f")
    pending, disabled = _source(session, "pending", "p"), _source(session, "disabled", "d")

    listed = {s.id for s in source_repo.get_monitorable_sources(session, discovery_role=True, access_method="web")}
    assert {active.id, failing.id} <= listed
    assert pending.id not in listed and disabled.id not in listed
    assert source_repo.get_monitorable_source_by_id(session, failing.id).id == failing.id
    assert source_repo.get_monitorable_source_by_id(session, active.id).id == active.id
    assert source_repo.get_monitorable_source_by_id(session, pending.id) is None
    assert source_repo.get_monitorable_source_by_id(session, disabled.id) is None

    only_api = source_repo.get_monitorable_sources(session, access_method="api")
    assert active.id not in {s.id for s in only_api}


def test_get_active_sources_and_get_source_by_id_still_exclude_failing(session):
    failing = _source(session, "failing")
    assert failing.id not in {s.id for s in source_repo.get_active_sources(session)}
    assert source_repo.get_source_by_id(session, failing.id) is None


# --- reactivate_source ---------------------------------------------------------------------


def test_reactivate_source_only_moves_failing_to_active(session):
    failing, disabled, pending, active = (_source(session, s, s) for s in ("failing", "disabled", "pending", "active"))
    assert source_repo.reactivate_source(session, failing.id).status == SourceStatus.ACTIVE
    for source, expected in ((disabled, SourceStatus.DISABLED), (pending, SourceStatus.PENDING), (active, SourceStatus.ACTIVE)):
        source_repo.reactivate_source(session, source.id)
        session.refresh(source)
        assert source.status == expected  # never resurrects a disabled/pending source
    assert source_repo.reactivate_source(session, uuid.uuid4()) is None


# --- consecutive_failure_count / bounded fetch-log -------------------------------------------


def test_consecutive_failure_count_ignores_rows_without_a_run_and_is_zero_with_no_history(session):
    source = _source(session, "active")
    assert source_repo.consecutive_failure_count(session, source.id) == 0
    source_repo.log_fetch(session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.FAIL, error="[store] x"))
    assert source_repo.consecutive_failure_count(session, source.id) == 0


def test_recent_fetch_logs_is_newest_first_and_limit_bounded(session):
    source = _source(session, "active")
    base = datetime(2027, 1, 1, tzinfo=timezone.utc)
    for i in range(5):
        source_repo.log_fetch(
            session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK, started_at=base + timedelta(minutes=i))
        )
    rows = source_repo.get_recent_fetch_logs(session, source.id, limit=3)
    assert [r.started_at for r in rows] == [base + timedelta(minutes=m) for m in (4, 3, 2)]


def test_log_fetch_stamps_a_distinct_python_side_started_at_when_omitted(session):
    """Within one transaction the DB `now()` default ties; run-ordering for the
    health streak needs strictly ordered timestamps."""
    source = _source(session, "active")
    a = source_repo.log_fetch(session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK))
    b = source_repo.log_fetch(session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK))
    assert b.started_at > a.started_at


def test_streak_ordering_uses_run_recency_even_when_created_in_one_transaction(session):
    source = _source(session, "active")
    outcomes = [FetchStatus.FAIL, FetchStatus.OK, FetchStatus.FAIL, FetchStatus.FAIL]  # oldest -> newest
    for status in outcomes:
        run = MonitoringRun(trigger=MonitoringTrigger.SCHEDULED)
        session.add(run)
        session.commit()
        source_repo.log_fetch(
            session, SourceFetchLogCreate(source_id=source.id, status=status, monitoring_run_id=run.id)
        )
    assert source_repo.consecutive_failure_count(session, source.id) == 2
