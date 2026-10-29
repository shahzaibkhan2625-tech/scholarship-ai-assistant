"""010 US1 T349: the six operator-only routes. anonymous -> 401, student -> 403 with NO side
effect, operator -> the same success as before the gate; the read routes stay open to students.
Real (rolled-back) DB."""

import uuid

import pytest

from app.data.repositories import monitoring_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import CandidateSource, CandidateSourceStatus
from app.schemas.source import CandidateSourceCreate


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


@pytest.fixture
def background_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("app.api.monitoring._execute_run", lambda run_id: calls.append(run_id))
    return calls


def _candidate(session):
    return source_repo.create_candidate_source(
        session, CandidateSourceCreate(url=f"https://gate-{uuid.uuid4().hex[:8]}.example.org/scholarships")
    )


def _approve_body(candidate):
    domain = candidate.url.split("//", 1)[1].split("/", 1)[0]
    return {
        "name": "Gate Test", "source_type": "gov", "official_status": "official", "domain": domain,
        "access_method": "web", "discovery_role": True, "reliability_level": "high",
    }


def _status(session, candidate_id):
    session.expire_all()
    return session.get(CandidateSource, candidate_id).status


def _run_count(session):
    session.expire_all()
    return session.query(MonitoringRun).count()


# --- anonymous -> 401 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", f"/sources/candidates/{uuid.uuid4()}/approve"),
        ("post", f"/sources/candidates/{uuid.uuid4()}/reject"),
        ("post", "/monitoring/runs"),
        ("get", f"/monitoring/runs/{uuid.uuid4()}"),
        ("get", f"/sources/{uuid.uuid4()}/fetch-log"),
        ("get", f"/sources/candidates/{uuid.uuid4()}/validations"),
    ],
)
def test_anonymous_is_401_on_every_operator_route(client, method, path):
    assert client.request(method.upper(), path, json={}).status_code == 401


# --- student -> 403, no side effect -----------------------------------------------------


def test_student_cannot_approve_and_the_candidate_stays_pending(client, session, student_headers):
    candidate = _candidate(session)
    response = client.post(
        f"/sources/candidates/{candidate.id}/approve", json=_approve_body(candidate), headers=student_headers
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "Operator access required"}
    assert _status(session, candidate.id) == CandidateSourceStatus.PENDING
    assert source_repo.get_source_by_domain(session, _approve_body(candidate)["domain"]) is None


def test_student_cannot_reject_and_the_candidate_stays_pending(client, session, student_headers):
    candidate = _candidate(session)
    response = client.post(
        f"/sources/candidates/{candidate.id}/reject", json={"reason": "nope"}, headers=student_headers
    )
    assert response.status_code == 403
    assert _status(session, candidate.id) == CandidateSourceStatus.PENDING


def test_student_cannot_start_a_run_and_no_run_row_or_task_appears(client, session, student_headers, background_calls):
    monitoring_repo.fail_leftover_running_runs(session)
    before = _run_count(session)
    response = client.post("/monitoring/runs", headers=student_headers)
    assert response.status_code == 403
    assert _run_count(session) == before
    assert background_calls == []


def test_student_gets_no_run_detail_even_for_an_existing_run(client, session, student_headers):
    run = monitoring_repo.create_run(session, MonitoringTrigger.MANUAL)
    response = client.get(f"/monitoring/runs/{run.id}", headers=student_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Operator access required"}


def test_student_gets_no_fetch_log_data(client, session, student_headers):
    source = _a_source(session)
    response = client.get(f"/sources/{source.id}/fetch-log", headers=student_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Operator access required"}


def test_student_gets_no_validation_history(client, session, student_headers):
    candidate = _candidate(session)
    response = client.get(f"/sources/candidates/{candidate.id}/validations", headers=student_headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Operator access required"}


def _a_source(session):
    from app.schemas.source import SourceRegistryCreate

    return source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Gate Source", source_type="gov", official_status="official",
            domain=f"gate-{uuid.uuid4().hex[:8]}.example.org", access_method="web", reliability_level="high",
        ),
    )


# --- operator -> same success as before -------------------------------------------------


def test_operator_approves(client, session, operator_headers):
    candidate = _candidate(session)
    response = client.post(
        f"/sources/candidates/{candidate.id}/approve", json=_approve_body(candidate), headers=operator_headers
    )
    assert response.status_code == 200
    assert response.json()["domain"] == _approve_body(candidate)["domain"]
    assert _status(session, candidate.id) == CandidateSourceStatus.APPROVED


def test_operator_rejects(client, session, operator_headers):
    candidate = _candidate(session)
    response = client.post(
        f"/sources/candidates/{candidate.id}/reject", json={"reason": "not official"}, headers=operator_headers
    )
    assert response.status_code == 200
    assert _status(session, candidate.id) == CandidateSourceStatus.REJECTED


def test_operator_starts_a_run(client, session, operator_headers, background_calls):
    monitoring_repo.fail_leftover_running_runs(session)
    response = client.post("/monitoring/runs", headers=operator_headers)
    assert response.status_code == 202
    assert background_calls == [uuid.UUID(response.json()["id"])]


def test_operator_reads_run_detail(client, session, operator_headers):
    run = monitoring_repo.create_run(session, MonitoringTrigger.MANUAL)
    response = client.get(f"/monitoring/runs/{run.id}", headers=operator_headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(run.id) and response.json()["source_outcomes"] == []


def test_operator_reads_fetch_log(client, session, operator_headers):
    source = _a_source(session)
    response = client.get(f"/sources/{source.id}/fetch-log", headers=operator_headers)
    assert response.status_code == 200 and response.json() == []


def test_operator_reads_validations(client, session, operator_headers):
    candidate = _candidate(session)
    response = client.get(f"/sources/candidates/{candidate.id}/validations", headers=operator_headers)
    assert response.status_code == 200 and response.json() == []


# --- routes that stay open to a student -------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/sources", "/sources/candidates", "/sources/health", "/monitoring/runs", "/coverage"]
)
def test_read_routes_stay_open_to_a_student(client, student_headers, path):
    assert client.get(path, headers=student_headers).status_code == 200
