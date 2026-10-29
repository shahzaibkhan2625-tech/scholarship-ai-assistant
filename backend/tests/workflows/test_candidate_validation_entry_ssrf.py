"""010 T365: both entry points that validate a pending candidate (manual `POST /monitoring/runs`
and the scheduler tick `_run_scheduled_monitor`) hit the same per-hop guard. One pending candidate
whose URL redirects to a private address; the web_fetch seams are faked, and the recording
transport must never see the private address. Runs in the rolled-back DB transaction."""

import socket
import uuid

import httpx
import pytest

from app.data.repositories import candidate_validation_repo, source_repo
from app.schemas.source import CandidateSourceCreate
from app.scheduling import scheduler
from app.tools import web_fetch

# conftest's autouse fixture empties candidate selection per test; keep the real function (see
# tests/workflows/test_source_monitor.py `_REAL_SELECT_PENDING`).
_REAL_SELECT_PENDING = candidate_validation_repo.list_pending_needing_validation

PUBLIC_IP = "93.184.216.34"
PRIVATE_TARGET = "https://10.0.0.8/admin"  # https -> https: the address check, not the downgrade rule, refuses it


@pytest.fixture
def entry_env(db_session_factory, monkeypatch):
    session = db_session_factory()
    host = f"cand-{uuid.uuid4().hex[:8]}.example.org"
    candidate = source_repo.create_candidate_source(session, CandidateSourceCreate(url=f"https://{host}/scholarships"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["Host"] == host:
            return httpx.Response(302, headers={"Location": PRIVATE_TARGET})
        return httpx.Response(200, text="<html></html>")

    def resolve(name: str) -> list[str]:
        if name == host:
            return [PUBLIC_IP]
        raise socket.gaierror(name)

    real = web_fetch._make_client
    monkeypatch.setattr(web_fetch, "_make_client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(web_fetch, "_resolve_host", resolve)
    # the candidate pre-check resolves through the same low-level lookup
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(name, *args, **kwargs):
        if name == host:
            return [(socket.AF_INET, 0, 0, "", (address, 0)) for address in resolve(name)]
        return real_getaddrinfo(name, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    # conftest empties candidate selection for every test; scope it to this candidate instead
    monkeypatch.setattr(
        candidate_validation_repo,
        "list_pending_needing_validation",
        lambda db, *, stale_before, limit: [i for i in _REAL_SELECT_PENDING(db, stale_before=stale_before, limit=10_000) if i == candidate.id][:limit],
    )
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda db, **kw: [])  # no registry sources in the run
    # background bodies open their own sessions: hand them the test's rolled-back connection
    monkeypatch.setattr("app.api.monitoring.SessionLocal", db_session_factory)
    monkeypatch.setattr("app.data.repositories.db.SessionLocal", db_session_factory)

    class Env:
        pass

    env = Env()
    env.session, env.candidate, env.requests = session, candidate, requests
    yield env
    session.close()


def _assert_blocked_and_never_requested(env):
    row = candidate_validation_repo.latest_for_candidate(env.session, env.candidate.id)
    assert row is not None
    assert row.reachable is False
    assert row.reachable_detail.startswith("blocked: ")
    assert "10.0.0.8" in row.reachable_detail
    assert [r.url.host for r in env.requests] == [PUBLIC_IP]  # the redirect target was never requested


def test_manual_run_entry_point_blocks_the_private_redirect(entry_env, client, make_user, monkeypatch):
    import app.api.monitoring as monitoring_api

    operator = make_user(is_operator=True)
    started: list[uuid.UUID] = []
    real_execute_run = monitoring_api._execute_run
    # The route hands `_execute_run` to a background task that, in this shared-connection harness,
    # would finish before the request session closes and be rolled back with it. Capture the run id
    # and run the real background body (its own session, as in production) after the response.
    monkeypatch.setattr(monitoring_api, "_execute_run", started.append)

    response = client.post("/monitoring/runs", headers=operator["headers"])

    assert response.status_code == 202
    (run_id,) = started
    real_execute_run(run_id)
    _assert_blocked_and_never_requested(entry_env)


def test_scheduler_tick_entry_point_blocks_the_private_redirect(entry_env):
    scheduler._run_scheduled_monitor()

    _assert_blocked_and_never_requested(entry_env)
