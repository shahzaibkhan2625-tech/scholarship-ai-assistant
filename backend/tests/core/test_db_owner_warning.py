"""Start-up owner-rights warning (010 US3, FR-DBROLE-5): one WARNING naming the flags, never blocks."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core import db_role_check

FLAGS = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls", "can_create_in_public")


class _Result:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class FakeConnection:
    """Reports one pg_roles row; a real Postgres is not needed for the decision logic."""

    def __init__(self, row=None, error=None):
        self._row, self._error = row, error

    def execute(self, statement, *args, **kwargs):
        if self._error:
            raise self._error
        return _Result(self._row)


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("index", range(len(FLAGS)))
def test_each_owner_flag_logs_one_warning_naming_it(caplog, index):
    row = tuple(i == index for i in range(len(FLAGS)))
    with caplog.at_level(logging.WARNING):
        flagged = db_role_check.check_db_role(FakeConnection(row))
    records = _warnings(caplog)
    assert len(records) == 1
    assert "database credentials have owner-level rights" in records[0].getMessage()
    assert FLAGS[index] in records[0].getMessage()
    assert flagged == [FLAGS[index]]


def test_several_flags_are_all_named_in_one_warning(caplog):
    with caplog.at_level(logging.WARNING):
        db_role_check.check_db_role(FakeConnection((True, True, True, True, True)))
    records = _warnings(caplog)
    assert len(records) == 1
    assert all(flag in records[0].getMessage() for flag in FLAGS)


def test_restricted_role_logs_nothing(caplog):
    with caplog.at_level(logging.DEBUG):
        flagged = db_role_check.check_db_role(FakeConnection((False,) * len(FLAGS)))
    assert flagged == []
    assert caplog.records == []


def test_query_failure_never_raises_and_leaks_no_url(caplog):
    error = RuntimeError("could not connect to postgresql://user:hunter2@host/db")  # allow-secret
    with caplog.at_level(logging.DEBUG):
        assert db_role_check.check_db_role(FakeConnection(error=error)) == []
    assert "hunter2" not in caplog.text


def test_lifespan_calls_the_check_once_and_a_failing_check_does_not_block_start(monkeypatch):
    from app.main import app

    calls = []

    def boom(*_args, **_kwargs):
        calls.append(1)
        raise RuntimeError("check exploded")

    monkeypatch.setattr(db_role_check, "check_current_db_role", boom)
    monkeypatch.setattr("app.scheduling.scheduler.start_scheduler", lambda: None)
    monkeypatch.setattr("app.scheduling.scheduler.stop_scheduler", lambda: None)
    monkeypatch.setattr("app.data.repositories.monitoring_repo.fail_leftover_running_runs", lambda _s: 0)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert calls == [1]
