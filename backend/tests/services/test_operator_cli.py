"""010 US1 T350: `python -m app.services.operator grant|revoke --email`, tested from `main(argv)`.
Real (rolled-back) DB: the CLI's SessionLocal is pointed at the test transaction."""

import uuid

import pytest

from app.models.audit import AuditLog
from app.models.user import User


@pytest.fixture
def cli(monkeypatch, db_session_factory, capsys):
    from app.data.repositories import db as db_mod
    from app.services import operator

    monkeypatch.setattr(db_mod, "SessionLocal", db_session_factory)

    def run(*argv: str) -> tuple[int, str]:
        capsys.readouterr()
        try:
            code = operator.main(list(argv))
        except SystemExit as exc:  # argparse exits 2 on bad arguments
            code = exc.code
        return code, capsys.readouterr().out.strip()

    return run


def _is_operator(db_session_factory, user_id) -> bool:
    with db_session_factory() as session:
        return session.get(User, user_id).is_operator


def _audit_rows(db_session_factory, user_id=None):
    with db_session_factory() as session:
        query = session.query(AuditLog).filter(AuditLog.action == "operator.grant")
        if user_id is not None:
            query = query.filter(AuditLog.user_id == user_id)
        return query.all()


def test_grant_makes_a_user_an_operator_and_writes_one_audit_row(cli, make_user, db_session_factory):
    user = make_user()
    code, out = cli("grant", "--email", user["email"])
    assert (code, out) == (0, f"granted operator status to {user['email']}")
    assert _is_operator(db_session_factory, user["id"]) is True
    rows = _audit_rows(db_session_factory, user["id"])
    assert len(rows) == 1
    assert rows[0].entity == f"user:{user['id']}"


def test_regrant_is_a_no_op_with_no_second_audit_row(cli, make_user, db_session_factory):
    user = make_user()
    cli("grant", "--email", user["email"])
    code, out = cli("grant", "--email", user["email"])
    assert (code, out) == (0, f"{user['email']} is already an operator")
    assert len(_audit_rows(db_session_factory, user["id"])) == 1


def test_revoke_removes_operator_status_without_an_audit_row(cli, make_user, db_session_factory):
    user = make_user(is_operator=True)
    code, out = cli("revoke", "--email", user["email"])
    assert (code, out) == (0, f"revoked operator status from {user['email']}")
    assert _is_operator(db_session_factory, user["id"]) is False
    assert _audit_rows(db_session_factory, user["id"]) == []


def test_revoke_of_a_non_operator_is_a_no_op(cli, make_user, db_session_factory):
    user = make_user()
    code, out = cli("revoke", "--email", user["email"])
    assert (code, out) == (0, f"{user['email']} is not an operator")
    assert _is_operator(db_session_factory, user["id"]) is False


@pytest.mark.parametrize("verb", ["grant", "revoke"])
def test_unknown_email_exits_1_and_changes_nothing(cli, db_session_factory, verb):
    before = len(_audit_rows(db_session_factory))
    email = f"nobody-{uuid.uuid4().hex}@example.com"
    code, out = cli(verb, "--email", email)
    assert (code, out) == (1, f"no such user: {email}")
    assert len(_audit_rows(db_session_factory)) == before


@pytest.mark.parametrize(
    "argv", [(), ("grant",), ("grant", "--email"), ("promote", "--email", "a@b.co"), ("grant", "--all")]
)
def test_bad_arguments_exit_2_and_change_nothing(cli, db_session_factory, argv):
    before = len(_audit_rows(db_session_factory))
    code, _ = cli(*argv)
    assert code == 2
    assert len(_audit_rows(db_session_factory)) == before


def test_email_is_trimmed_and_matched_case_insensitively(cli, make_user, db_session_factory):
    user = make_user()
    shouty = f"  {user['email'].upper()} "
    code, out = cli("grant", "--email", shouty)
    assert code == 0
    assert out == f"granted operator status to {user['email']}"
    assert _is_operator(db_session_factory, user["id"]) is True


def test_output_never_contains_credentials(cli, make_user, db_session_factory):
    user = make_user()
    with db_session_factory() as session:
        password_hash = session.get(User, user["id"]).password_hash
    _, out = cli("grant", "--email", user["email"])
    assert password_hash not in out
    assert "Bearer" not in out and user["headers"]["Authorization"].split()[1] not in out


def test_grant_applies_to_the_next_request_and_revoke_takes_it_away(cli, client, make_user):
    user, other = make_user(), make_user()
    assert client.get(f"/sources/{uuid.uuid4()}/fetch-log", headers=user["headers"]).status_code == 403
    cli("grant", "--email", user["email"])
    assert client.get(f"/sources/{uuid.uuid4()}/fetch-log", headers=user["headers"]).status_code == 404
    assert client.get(f"/sources/{uuid.uuid4()}/fetch-log", headers=other["headers"]).status_code == 403
    cli("revoke", "--email", user["email"])
    assert client.get(f"/sources/{uuid.uuid4()}/fetch-log", headers=user["headers"]).status_code == 403


def test_a_run_started_before_the_revoke_still_finishes(cli, client, make_user, db_session_factory, monkeypatch):
    """The background body never re-checks the flag: it carries only the run id."""
    from app.api import monitoring
    from app.data.repositories import monitoring_repo

    started, finished = [], []
    real_execute = monitoring._execute_run
    monkeypatch.setattr(monitoring, "_execute_run", lambda run_id: started.append(run_id))
    operator_user = make_user(is_operator=True)
    with db_session_factory() as session:
        monitoring_repo.fail_leftover_running_runs(session)
    response = client.post("/monitoring/runs", headers=operator_user["headers"])
    assert response.status_code == 202 and len(started) == 1

    cli("revoke", "--email", operator_user["email"])
    assert client.post("/monitoring/runs", headers=operator_user["headers"]).status_code == 403

    monkeypatch.setattr(monitoring, "SessionLocal", db_session_factory)
    monkeypatch.setattr(monitoring, "run_source_monitor", lambda session, **kw: finished.append(kw["run_id"]))
    real_execute(started[0])
    assert finished == started
