"""010 T336: the security-hardening revision, checked against a LOCAL/CI Postgres that has
been migrated to head (`alembic upgrade head`). Skips without a reachable local database.
Downgrade is exercised inside a transaction that is rolled back (Postgres DDL is transactional),
so the shared test database is left at head."""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
PRIOR_HEAD = "f2b8d4a6c1e3"


def _load_revision():
    paths = list(VERSIONS.glob("*_security_hardening.py"))
    assert len(paths) == 1, "expected exactly one '*_security_hardening.py' revision"
    spec = importlib.util.spec_from_file_location("security_hardening_revision", paths[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connection(db_session_factory):
    from app.data.repositories.db import engine

    with engine.connect() as conn:
        outer = conn.begin()
        try:
            yield conn
        finally:
            outer.rollback()


def _scalar(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar()


def test_revision_chains_from_the_prior_head():
    module = _load_revision()
    assert module.down_revision == PRIOR_HEAD


def test_users_is_operator_column(connection):
    row = connection.execute(
        text(
            "SELECT is_nullable, column_default, data_type FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'is_operator'"
        )
    ).one()
    assert row.is_nullable == "NO"
    assert row.data_type == "boolean"
    assert row.column_default == "false"


def test_new_user_defaults_to_non_operator(connection):
    connection.execute(
        text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')"),
        {"id": uuid.uuid4(), "email": f"d-{uuid.uuid4().hex}@example.com"},
    )
    assert _scalar(connection, "SELECT bool_and(NOT is_operator) FROM users") is True


def test_email_unique_index_is_case_insensitive(connection):
    tag = uuid.uuid4().hex
    insert = text("INSERT INTO users (id, email, password_hash) VALUES (:id, :email, 'x')")
    connection.execute(insert, {"id": uuid.uuid4(), "email": f"Ada-{tag}@x.com"})
    nested = connection.begin_nested()
    with pytest.raises(IntegrityError):
        connection.execute(insert, {"id": uuid.uuid4(), "email": f"ada-{tag}@x.com"})
    nested.rollback()


def test_lower_email_index_exists_and_is_unique(connection):
    definition = _scalar(
        connection,
        "SELECT indexdef FROM pg_indexes WHERE tablename = 'users' AND indexdef ILIKE '%lower(%email%'",
    )
    assert definition is not None and "UNIQUE" in definition.upper()


def test_audit_log_columns(connection):
    columns = {
        r.column_name: (r.data_type, r.is_nullable)
        for r in connection.execute(
            text("SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'audit_log'")
        )
    }
    assert columns == {
        "id": ("uuid", "NO"),
        "user_id": ("uuid", "NO"),
        "action": ("text", "NO"),
        "entity": ("text", "NO"),
        "at": ("timestamp with time zone", "NO"),
    }


def test_audit_log_has_no_foreign_key(connection):
    count = _scalar(
        connection,
        "SELECT count(*) FROM information_schema.table_constraints "
        "WHERE table_name = 'audit_log' AND constraint_type = 'FOREIGN KEY'",
    )
    assert count == 0


def test_audit_log_indexes(connection):
    definitions = [
        r.indexdef for r in connection.execute(text("SELECT indexdef FROM pg_indexes WHERE tablename = 'audit_log'"))
    ]
    assert any("(at)" in d for d in definitions), definitions
    assert any("(user_id, at)" in d for d in definitions), definitions


def test_audit_log_at_defaults_to_now(connection):
    uid = uuid.uuid4()
    connection.execute(
        text("INSERT INTO audit_log (id, user_id, action, entity) VALUES (:id, :u, 'user.signup', :e)"),
        {"id": uuid.uuid4(), "u": uid, "e": f"user:{uid}"},
    )
    assert _scalar(connection, "SELECT at IS NOT NULL FROM audit_log WHERE user_id = :u", u=uid) is True


def test_conditional_revoke_does_not_fail_when_the_role_is_absent(connection):
    module = _load_revision()
    role_exists = _scalar(connection, "SELECT count(*) FROM pg_roles WHERE rolname = 'scholarship_app'")
    if role_exists:
        pytest.skip("scholarship_app exists here; the absent-role path is covered where it does not")
    connection.execute(text(module.REVOKE_AUDIT_LOG_SQL))


def test_downgrade_removes_column_index_and_table_and_upgrade_restores_them(connection):
    module = _load_revision()
    ops = Operations(MigrationContext.configure(connection))
    with Operations.context(ops.migration_context):
        module.downgrade()
    assert _scalar(connection, "SELECT to_regclass('public.audit_log')") is None
    assert (
        _scalar(
            connection,
            "SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'is_operator'",
        )
        == 0
    )
    assert _scalar(connection, "SELECT count(*) FROM pg_indexes WHERE tablename = 'users' AND indexdef ILIKE '%lower(%'") == 0
    with Operations.context(ops.migration_context):
        module.upgrade()
    assert _scalar(connection, "SELECT to_regclass('public.audit_log')") is not None
