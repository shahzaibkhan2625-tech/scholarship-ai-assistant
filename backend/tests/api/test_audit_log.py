"""010 US6 T406: the six audited actions, each started at its entry point. The audit row is
written in the action's own transaction, only for a successful action, and a failing audit
insert aborts the action. Real (rolled-back) DB."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import audit_repo, source_repo
from app.models.application import Application, SubmissionApproval
from app.models.audit import AuditLog
from app.models.monitoring import MonitoringRun
from app.models.scholarship import Scholarship
from app.models.source import CandidateSource, CandidateSourceStatus, SourceRegistry
from app.models.user import User
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


@pytest.fixture
def cli(monkeypatch, db_session_factory, capsys):
    from app.data.repositories import db as db_mod
    from app.services import operator

    monkeypatch.setattr(db_mod, "SessionLocal", db_session_factory)

    def run(*argv: str) -> int:
        capsys.readouterr()
        return operator.main(list(argv))

    return run


@pytest.fixture
def failing_audit(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("audit insert failed")

    monkeypatch.setattr(audit_repo, "record", boom)


def _rows(session, action=None, user_id=None):
    session.expire_all()
    query = session.query(AuditLog)
    if action is not None:
        query = query.filter(AuditLog.action == action)
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    return query.all()


def _candidate(session):
    return source_repo.create_candidate_source(
        session, CandidateSourceCreate(url=f"https://audit-{uuid.uuid4().hex[:8]}.example.org/scholarships")
    )


def _approve_body(candidate):
    domain = candidate.url.split("//", 1)[1].split("/", 1)[0]
    return {
        "name": "Audit Test", "source_type": "gov", "official_status": "official", "domain": domain,
        "access_method": "web", "discovery_role": True, "reliability_level": "high",
    }


@pytest.fixture
def application_for(session):
    scholarship = Scholarship(name="Audit Test Scholarship", official_scholarship_url="https://example.test/audit")
    session.add(scholarship)
    session.commit()

    def _create(user_id):
        row = Application(user_id=user_id, scholarship_id=scholarship.id)
        session.add(row)
        session.commit()
        return row.id

    return _create


# --- user.signup -----------------------------------------------------------------------


def test_signup_writes_one_row_for_the_new_user(client, session):
    email = f"audit-{uuid.uuid4().hex}@example.com"
    response = client.post("/auth/signup", json={"email": email, "password": "a-long-enough-pass-123"})
    assert response.status_code == 201
    user_id = uuid.UUID(response.json()["id"])
    rows = _rows(session, "user.signup", user_id)
    assert len(rows) == 1
    assert rows[0].entity == f"user:{user_id}"
    assert abs(datetime.now(timezone.utc) - rows[0].at) < timedelta(seconds=5)


def test_duplicate_signup_409_and_invalid_signup_422_leave_no_row(client, session, make_user):
    existing = make_user()
    before = len(_rows(session))
    duplicate = client.post("/auth/signup", json={"email": existing["email"], "password": "a-long-enough-pass-123"})
    assert duplicate.status_code == 409
    assert client.post("/auth/signup", json={"email": "not-an-email", "password": "x"}).status_code == 422
    assert len(_rows(session)) == before


def test_failing_audit_insert_aborts_signup(client, session, failing_audit):
    email = f"audit-{uuid.uuid4().hex}@example.com"
    with pytest.raises(RuntimeError, match="audit insert failed"):
        client.post("/auth/signup", json={"email": email, "password": "a-long-enough-pass-123"})
    session.expire_all()
    assert session.query(User).filter(User.email == email).count() == 0


# --- source.approve / source.reject ------------------------------------------------------


def test_operator_approve_writes_source_approve(client, session, make_user):
    operator = make_user(is_operator=True)
    candidate = _candidate(session)
    response = client.post(
        f"/sources/candidates/{candidate.id}/approve", json=_approve_body(candidate), headers=operator["headers"]
    )
    assert response.status_code == 200
    rows = _rows(session, "source.approve")
    assert [(r.user_id, r.entity) for r in rows] == [(operator["id"], f"candidate_source:{candidate.id}")]


def test_operator_reject_writes_source_reject(client, session, make_user):
    operator = make_user(is_operator=True)
    candidate = _candidate(session)
    response = client.post(
        f"/sources/candidates/{candidate.id}/reject", json={"reason": "not official"}, headers=operator["headers"]
    )
    assert response.status_code == 200
    rows = _rows(session, "source.reject")
    assert [(r.user_id, r.entity) for r in rows] == [(operator["id"], f"candidate_source:{candidate.id}")]


def test_refused_source_actions_leave_zero_rows(client, session, make_user):
    operator, student = make_user(is_operator=True), make_user()
    candidate = _candidate(session)
    body = _approve_body(candidate)
    path = f"/sources/candidates/{candidate.id}"
    assert client.post(f"{path}/approve", json=body, headers=student["headers"]).status_code == 403
    assert client.post(f"{path}/reject", json={"reason": "x"}, headers=student["headers"]).status_code == 403
    assert client.post(f"{path}/reject", json={"reason": "x"}, headers=operator["headers"]).status_code == 200
    # a second review of the same candidate is a 409 and adds no row
    assert client.post(f"{path}/approve", json=body, headers=operator["headers"]).status_code == 409
    assert client.post(f"{path}/reject", json={}, headers=operator["headers"]).status_code == 422
    assert [r.action for r in _rows(session) if r.entity == f"candidate_source:{candidate.id}"] == ["source.reject"]


def test_failing_audit_insert_aborts_approve_and_reject(client, session, make_user, failing_audit):
    operator = make_user(is_operator=True)
    first, second = _candidate(session), _candidate(session)
    with pytest.raises(RuntimeError, match="audit insert failed"):
        client.post(f"/sources/candidates/{first.id}/approve", json=_approve_body(first), headers=operator["headers"])
    with pytest.raises(RuntimeError, match="audit insert failed"):
        client.post(f"/sources/candidates/{second.id}/reject", json={"reason": "x"}, headers=operator["headers"])
    session.expire_all()
    assert session.get(CandidateSource, first.id).status == CandidateSourceStatus.PENDING
    assert session.get(CandidateSource, second.id).status == CandidateSourceStatus.PENDING
    assert session.query(SourceRegistry).filter(SourceRegistry.domain == _approve_body(first)["domain"]).count() == 0


# --- monitoring.run.manual -----------------------------------------------------------------


def test_operator_run_writes_monitoring_run_manual(client, session, make_user, background_calls):
    operator = make_user(is_operator=True)
    response = client.post("/monitoring/runs", headers=operator["headers"])
    assert response.status_code == 202
    run_id = response.json()["id"]
    rows = _rows(session, "monitoring.run.manual")
    assert [(r.user_id, r.entity) for r in rows] == [(operator["id"], f"monitoring_run:{run_id}")]


def test_refused_and_conflicting_runs_leave_zero_rows(client, session, make_user, background_calls):
    operator, student = make_user(is_operator=True), make_user()
    assert client.post("/monitoring/runs", headers=student["headers"]).status_code == 403
    assert len(_rows(session, "monitoring.run.manual")) == 0
    assert client.post("/monitoring/runs", headers=operator["headers"]).status_code == 202
    assert client.post("/monitoring/runs", headers=operator["headers"]).status_code == 409  # one already running
    assert len(_rows(session, "monitoring.run.manual")) == 1


def test_a_run_started_before_revocation_keeps_its_record(client, session, make_user, background_calls, cli):
    operator = make_user(is_operator=True)
    assert client.post("/monitoring/runs", headers=operator["headers"]).status_code == 202
    assert cli("revoke", "--email", operator["email"]) == 0
    assert client.post("/monitoring/runs", headers=operator["headers"]).status_code == 403
    rows = _rows(session, "monitoring.run.manual", operator["id"])
    assert len(rows) == 1 and rows[0].entity.startswith("monitoring_run:")


def test_failing_audit_insert_aborts_the_run(client, session, make_user, background_calls, failing_audit):
    operator = make_user(is_operator=True)
    with pytest.raises(RuntimeError, match="audit insert failed"):
        client.post("/monitoring/runs", headers=operator["headers"])
    session.expire_all()
    assert session.query(MonitoringRun).count() == 0
    assert background_calls == []


def test_scheduled_run_writes_no_audit_row(session, db_session_factory, monkeypatch):
    from app.data.repositories import db as db_mod
    from app.scheduling import scheduler

    monkeypatch.setattr(db_mod, "SessionLocal", db_session_factory)
    before = len(_rows(session, "monitoring.run.manual"))
    scheduler._run_scheduled_monitor()
    session.expire_all()
    assert session.query(MonitoringRun).count() >= 1  # the tick did run
    assert len(_rows(session, "monitoring.run.manual")) == before


# --- submission.approve ----------------------------------------------------------------------


def test_submission_approval_writes_submission_approve(client, session, make_user, application_for):
    student = make_user()
    application_id = application_for(student["id"])
    response = client.post(
        f"/applications/{application_id}/submission-approvals",
        json={"submission_scope": "final-submission"},
        headers=student["headers"],
    )
    assert response.status_code == 201
    rows = _rows(session, "submission.approve")
    assert [(r.user_id, r.entity) for r in rows] == [(student["id"], f"submission_approval:{response.json()['id']}")]


def test_refused_submission_approvals_leave_zero_rows(client, session, make_user, application_for):
    owner, intruder = make_user(), make_user()
    application_id = application_for(owner["id"])
    path = f"/applications/{application_id}/submission-approvals"
    assert client.post(path, json={"submission_scope": "x"}, headers=intruder["headers"]).status_code == 404
    assert client.post(path, json={}, headers=owner["headers"]).status_code == 422
    assert len(_rows(session, "submission.approve")) == 0


def test_failing_audit_insert_aborts_the_approval(client, session, make_user, application_for, failing_audit):
    student = make_user()
    application_id = application_for(student["id"])
    with pytest.raises(RuntimeError, match="audit insert failed"):
        client.post(
            f"/applications/{application_id}/submission-approvals",
            json={"submission_scope": "final-submission"},
            headers=student["headers"],
        )
    session.expire_all()
    assert session.query(SubmissionApproval).count() == 0


# --- operator.grant ---------------------------------------------------------------------------


def test_cli_grant_writes_operator_grant(cli, session, make_user):
    user = make_user()
    assert cli("grant", "--email", user["email"]) == 0
    assert [r.entity for r in _rows(session, "operator.grant", user["id"])] == [f"user:{user['id']}"]


def test_failing_audit_insert_aborts_the_grant(cli, session, make_user, failing_audit):
    user = make_user()
    with pytest.raises(RuntimeError, match="audit insert failed"):
        cli("grant", "--email", user["email"])
    session.expire_all()
    assert session.get(User, user["id"]).is_operator is False
