"""Contract tests: GET /sources/health and GET /sources/{id}/fetch-log
(002 US3 T197, FR-HEALTH-1..4, FR-FETCH-3). Real (rolled-back) DB.

`consecutive_failures` counts monitoring-RUN outcomes only (ADR-0008): one
outcome per source per run, discovery rows (NULL `monitoring_run_id`) ignored.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import FetchStatus, SourceStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate

_BASE = datetime(2027, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _source(session, label="h", **overrides):
    data = dict(
        name=f"Health Test {label}", source_type="gov", official_status="official",
        domain=f"health-{label}-{uuid.uuid4().hex[:8]}.example.com", access_method="web",
        discovery_role=True, reliability_level="high", status="active",
    )
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**{**data, **overrides}))


def _run(session, offset_minutes):
    run = MonitoringRun(trigger=MonitoringTrigger.SCHEDULED, started_at=_BASE + timedelta(minutes=offset_minutes))
    session.add(run)
    session.commit()
    return run


def _log(session, source, run, status, minute, **kw):
    return source_repo.log_fetch(
        session,
        SourceFetchLogCreate(
            source_id=source.id, status=status, monitoring_run_id=run.id if run else None,
            started_at=_BASE + timedelta(minutes=minute), fetched_url=f"https://{source.domain}/list", **kw,
        ),
    )


def _health(client, headers, source, **params):
    response = client.get("/sources/health", headers=headers, params=params)
    assert response.status_code == 200
    return response, next((row for row in response.json() if row["source_id"] == str(source.id)), None)


# --- auth / routing ----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/sources/health", f"/sources/{uuid.uuid4()}/fetch-log"])
def test_health_routes_require_a_bearer_token(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_health_route_is_not_shadowed_by_a_sources_path_parameter(authed_user):
    from app.main import app

    paths = []
    for route in app.routes:
        prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
        for sub in getattr(getattr(route, "original_router", None), "routes", None) or [route]:
            if "GET" in (getattr(sub, "methods", None) or set()):
                paths.append(f"{prefix}{getattr(sub, 'path', '')}")
    assert paths.index("/sources/health") < min(i for i, p in enumerate(paths) if p.startswith("/sources/{"))
    assert authed_user["client"].get("/sources/health", headers=authed_user["headers"]).status_code == 200


# --- GET /sources/health -------------------------------------------------------------


def test_health_lists_every_source_including_disabled_with_required_fields(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    active = _source(session, "act")
    failing = _source(session, "fail", status="failing")
    dis_domain = f"health-dis-{uuid.uuid4().hex[:8]}.example.com"
    disabled = _source(
        session, "dis", status="disabled", domain=dis_domain, listing_page_url=f"https://{dis_domain}/list"
    )

    response, _ = _health(client, headers, active)
    by_id = {row["source_id"]: row for row in response.json()}
    for source, expected in ((active, "active"), (failing, "failing"), (disabled, "disabled")):
        row = by_id[str(source.id)]
        assert row["status"] == expected and row["name"] == source.name
        assert {"last_checked_at", "last_success_at", "consecutive_failures", "listing_page_url"} <= set(row)
        assert row["consecutive_failures"] == 0
    assert by_id[str(disabled.id)]["listing_page_url"] == f"https://{dis_domain}/list"


def test_pending_sources_are_excluded_from_health(authed_user, session):
    pending = _source(session, "pend", status="pending")
    response, row = _health(authed_user["client"], authed_user["headers"], pending)
    assert row is None
    assert {r["status"] for r in response.json()} <= {"active", "failing", "disabled"}


def test_status_filter_and_invalid_status(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    active, failing = _source(session, "a"), _source(session, "f", status="failing")
    response, _ = _health(client, headers, active, status="failing")
    ids = {r["source_id"] for r in response.json()}
    assert str(failing.id) in ids and str(active.id) not in ids
    assert {r["status"] for r in response.json()} == {"failing"}
    assert client.get("/sources/health", headers=headers, params={"status": "pending"}).status_code == 422
    assert client.get("/sources/health", headers=headers, params={"status": "bogus"}).status_code == 422


def test_timestamps_come_from_the_registry_not_from_a_zero_result_shape(authed_user, session):
    source = _source(session, "ts")
    source_repo.touch_source_check(session, source.id, success=True, now=_BASE)
    source_repo.touch_source_check(session, source.id, success=False, now=_BASE + timedelta(hours=1))
    _, row = _health(authed_user["client"], authed_user["headers"], source)
    assert row["last_success_at"].startswith("2027-01-01T00:00:00")
    assert row["last_checked_at"].startswith("2027-01-01T01:00:00")
    assert set(row) == {"source_id", "name", "status", "last_checked_at", "last_success_at",
                        "consecutive_failures", "listing_page_url"}  # no items/results field to misread


def test_consecutive_failures_counts_runs_not_rows(authed_user, session):
    """A failed monitored check writes an OK row (HTTP) then a FAIL row
    (extraction) in the SAME run: that is ONE failed outcome, not an alternating
    ok/fail sequence."""
    source = _source(session, "runs")
    for i in range(3):
        run = _run(session, i * 10)
        _log(session, source, run, FetchStatus.OK, i * 10)  # fetch_official's HTTP-200 row
        _log(session, source, run, FetchStatus.FAIL, i * 10 + 1, error="listing extraction yielded 0 candidates")
    _, row = _health(authed_user["client"], authed_user["headers"], source)
    assert row["consecutive_failures"] == 3


def test_a_later_success_resets_the_streak_and_older_failures_do_not_count(authed_user, session):
    source = _source(session, "reset")
    for i, status in enumerate([FetchStatus.FAIL, FetchStatus.FAIL, FetchStatus.OK, FetchStatus.FAIL]):
        _log(session, source, _run(session, i * 10), status, i * 10)
    _, row = _health(authed_user["client"], authed_user["headers"], source)
    assert row["consecutive_failures"] == 1  # only the newest run failed; the OK before it ends the streak


def test_discovery_and_ingestion_rows_do_not_affect_the_count(authed_user, session):
    source = _source(session, "noise")
    _log(session, source, _run(session, 0), FetchStatus.FAIL, 0)
    # NULL monitoring_run_id rows (discovery / ingestion stage rows) in between and after:
    _log(session, source, None, FetchStatus.OK, 5, items_found=1)
    _log(session, source, None, FetchStatus.FAIL, 6, error="[store] boom")
    _, row = _health(authed_user["client"], authed_user["headers"], source)
    assert row["consecutive_failures"] == 1


def test_timeout_counts_as_a_failed_outcome(authed_user, session):
    source = _source(session, "to")
    _log(session, source, _run(session, 0), FetchStatus.TIMEOUT, 0)
    _, row = _health(authed_user["client"], authed_user["headers"], source)
    assert row["consecutive_failures"] == 1


# --- GET /sources/{id}/fetch-log -------------------------------------------------------


def test_fetch_log_is_newest_first_and_exposes_url_and_fallback(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    source = _source(session, "log")
    for i in range(3):
        _log(session, source, _run(session, i), FetchStatus.OK, i, items_found=i)
    _log(session, source, None, FetchStatus.FAIL, 10, error="boom", used_homepage_fallback=True)

    response = client.get(f"/sources/{source.id}/fetch-log", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert [r["started_at"] for r in body] == sorted((r["started_at"] for r in body), reverse=True)
    assert body[0]["status"] == "fail" and body[0]["used_homepage_fallback"] is True
    assert body[0]["fetched_url"] == f"https://{source.domain}/list" and body[0]["error"] == "boom"
    assert body[1]["status"] == "ok" and body[1]["items_found"] == 2
    assert {"id", "source_id", "started_at", "status", "retry_count", "fetched_url", "used_homepage_fallback",
            "monitoring_run_id"} <= set(body[0])


def test_fetch_log_limit_default_and_bounds(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    source = _source(session, "lim")
    for i in range(55):
        _log(session, source, None, FetchStatus.OK, i)
    path = f"/sources/{source.id}/fetch-log"
    assert len(client.get(path, headers=headers).json()) == 50
    assert len(client.get(path, headers=headers, params={"limit": 3}).json()) == 3
    assert client.get(path, headers=headers, params={"limit": 200}).status_code == 200
    assert client.get(path, headers=headers, params={"limit": 201}).status_code == 422
    assert client.get(path, headers=headers, params={"limit": 0}).status_code == 422


def test_fetch_log_404_for_unknown_source_but_works_for_non_active(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    assert client.get(f"/sources/{uuid.uuid4()}/fetch-log", headers=headers).status_code == 404
    disabled = _source(session, "dlog", status="disabled")
    assert client.get(f"/sources/{disabled.id}/fetch-log", headers=headers).status_code == 200
