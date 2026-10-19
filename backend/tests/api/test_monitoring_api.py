"""Contract tests: /monitoring/runs (002 T173/T174/T181) and the app lifespan
wiring (T183). The workflow itself is mocked (it has its own tests in
tests/workflows/test_source_monitor.py); no scheduler tick ever fires."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.data.repositories import monitoring_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringStatus, MonitoringTrigger
from app.models.source import FetchStatus
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


# --- auth ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [("get", "/monitoring/runs"), ("get", f"/monitoring/runs/{uuid.uuid4()}"), ("post", "/monitoring/runs")],
)
def test_every_monitoring_route_requires_a_bearer_token(client, method, path):
    assert getattr(client, method)(path).status_code == 401
    assert getattr(client, method)(path, headers={"Authorization": "Bearer not-a-token"}).status_code == 401


# --- GET /monitoring/runs ---------------------------------------------------------


def test_list_runs_is_newest_first_default_limit_20(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    base = datetime.now(timezone.utc) + timedelta(days=2)
    ids = []
    for i in range(3):
        run = MonitoringRun(trigger=MonitoringTrigger.SCHEDULED, started_at=base + timedelta(seconds=i))
        session.add(run)
        session.flush()
        ids.append(str(run.id))
    session.commit()

    response = client.get("/monitoring/runs", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) <= 20
    assert [r["id"] for r in body[:3]] == list(reversed(ids))
    started = [r["started_at"] for r in body]
    assert started == sorted(started, reverse=True)
    assert {"id", "started_at", "ended_at", "trigger", "status", "sources_processed", "sources_failed",
            "changes_detected"} <= set(body[0])


def test_list_runs_limit_bounds(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    for _ in range(2):
        monitoring_repo.create_run(session, MonitoringTrigger.MANUAL)
    assert client.get("/monitoring/runs?limit=2", headers=headers).status_code == 200
    assert len(client.get("/monitoring/runs?limit=1", headers=headers).json()) == 1
    assert client.get("/monitoring/runs?limit=100", headers=headers).status_code == 200
    assert client.get("/monitoring/runs?limit=101", headers=headers).status_code == 422
    assert client.get("/monitoring/runs?limit=0", headers=headers).status_code == 422


# --- GET /monitoring/runs/{id} ----------------------------------------------------


def test_get_run_unknown_id_is_404(authed_user):
    response = authed_user["client"].get(f"/monitoring/runs/{uuid.uuid4()}", headers=authed_user["headers"])
    assert response.status_code == 404


def test_get_run_embeds_source_outcomes_from_fetch_logs_carrying_the_run_id(authed_user, session):
    client, headers = authed_user["client"], authed_user["headers"]
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="API Test", source_type="gov", official_status="official",
            domain=f"mon-api-{uuid.uuid4().hex[:10]}.example.com", access_method="web", reliability_level="high",
            status="active",
        ),
    )
    run = monitoring_repo.create_run(session, MonitoringTrigger.MANUAL)
    other_run = monitoring_repo.create_run(session, MonitoringTrigger.MANUAL)
    source_repo.log_fetch(session, SourceFetchLogCreate(
        source_id=source.id, status=FetchStatus.OK, items_found=2, fetched_url="https://a.example.com/",
        monitoring_run_id=run.id))
    source_repo.log_fetch(session, SourceFetchLogCreate(
        source_id=source.id, status=FetchStatus.FAIL, error="boom", monitoring_run_id=other_run.id))
    source_repo.log_fetch(session, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK))  # no run id

    response = client.get(f"/monitoring/runs/{run.id}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(run.id) and body["status"] == "running"
    [outcome] = body["source_outcomes"]
    assert outcome["monitoring_run_id"] == str(run.id) and outcome["items_found"] == 2
    assert outcome["source_id"] == str(source.id)


# --- POST /monitoring/runs --------------------------------------------------------


@pytest.fixture
def background_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("app.api.monitoring._execute_run", lambda run_id: calls.append(run_id))
    return calls


def test_post_starts_a_run_in_the_background_and_returns_202(authed_user, session, background_calls):
    monitoring_repo.fail_leftover_running_runs(session)  # a real running row would 409 (rolled back at teardown)
    response = authed_user["client"].post("/monitoring/runs", headers=authed_user["headers"])
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running" and body["trigger"] == "manual" and body["ended_at"] is None
    assert background_calls == [uuid.UUID(body["id"])]


def test_post_while_a_run_is_in_progress_is_409_and_starts_nothing(authed_user, session, background_calls):
    monitoring_repo.fail_leftover_running_runs(session)
    monitoring_repo.create_run(session, MonitoringTrigger.SCHEDULED)  # status running
    response = authed_user["client"].post("/monitoring/runs", headers=authed_user["headers"])
    assert response.status_code == 409
    assert background_calls == []


def test_background_body_runs_the_workflow_for_that_run_on_its_own_session(monkeypatch):
    import app.api.monitoring as api_mod

    seen = {}

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(api_mod, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(api_mod, "run_source_monitor", lambda s, **kw: seen.update(kw) or None)
    run_id = uuid.uuid4()
    api_mod._execute_run(run_id)
    assert seen == {"trigger": MonitoringTrigger.MANUAL, "run_id": run_id}


def test_background_body_swallows_workflow_errors(monkeypatch):
    import app.api.monitoring as api_mod

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def boom(s, **kw):
        raise RuntimeError("workflow crashed")

    monkeypatch.setattr(api_mod, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(api_mod, "run_source_monitor", boom)
    api_mod._execute_run(uuid.uuid4())  # must not raise (the workflow already marked the run failed)


# --- lifespan (T183) --------------------------------------------------------------


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_lifespan(monkeypatch, *, recover):
    import app.data.repositories.db as db_mod
    import app.scheduling.scheduler as sched_mod

    events = []
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(monitoring_repo, "fail_leftover_running_runs", recover(events))
    monkeypatch.setattr(sched_mod, "start_scheduler", lambda *a, **k: events.append("start"))
    monkeypatch.setattr(sched_mod, "stop_scheduler", lambda: events.append("stop"))
    return events


def test_lifespan_recovers_crashed_runs_then_starts_and_finally_stops_the_scheduler(monkeypatch):
    from app.main import app

    events = _patch_lifespan(monkeypatch, recover=lambda ev: lambda s: ev.append("recover") or 2)
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200
        assert events == ["recover", "start"]
    assert events == ["recover", "start", "stop"]


def test_lifespan_still_boots_when_crash_recovery_fails(monkeypatch):
    from app.main import app

    def failing(events):
        def _raise(session):
            raise RuntimeError("db down")

        return _raise

    events = _patch_lifespan(monkeypatch, recover=failing)
    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200
    assert events == ["start", "stop"]


def test_lifespan_with_interval_zero_starts_no_scheduler(monkeypatch):
    import app.data.repositories.db as db_mod
    import app.scheduling.scheduler as sched_mod
    from app.main import app

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(monitoring_repo, "fail_leftover_running_runs", lambda s: 0)
    monkeypatch.setattr(sched_mod.settings, "monitoring_interval_minutes", 0)
    monkeypatch.setattr(sched_mod, "_scheduler", None)
    with TestClient(app):
        assert sched_mod._scheduler is None


def test_monitoring_router_is_registered_once_on_the_app():
    from app.main import app
    from tests.conftest import find_registered_routes

    for method in ("GET", "POST"):
        assert len(find_registered_routes(app, method, "/monitoring/runs")) == 1
    assert len(find_registered_routes(app, "GET", "/monitoring/runs/{run_id}")) == 1
