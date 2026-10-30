"""The least-privilege application role (010 US3, FR-DBROLE-1, FR-DBROLE-3, SC-003).

Connected as `scholarship_app` (CI, or the local throw-away container after the role script),
DDL and role management are refused while DML works. Connected as an owner-level role these
tests skip with a stated reason: they prove nothing about the restricted role then."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from tests.db_guard import require_db_allowed

OWNER_SKIP = "connected role is owner-level; run with DATABASE_URL set to the scholarship_app role"
INSUFFICIENT_PRIVILEGE = "42501"
APP_ROLE = "scholarship_app"


@pytest.fixture(scope="module")
def app_engine():
    require_db_allowed()
    try:
        from app.data.repositories.db import engine
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping DB-dependent tests.")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, "
                    "has_schema_privilege(current_user, 'public', 'CREATE') "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
    except OperationalError:
        if make_url(str(engine.url)).username == APP_ROLE:
            pytest.fail(f"cannot connect as {APP_ROLE}: the role script did not run (or the password is wrong)")
        pytest.skip("Could not reach the configured database; skipping DB-dependent tests.")
    if any(row):
        pytest.skip(OWNER_SKIP)
    return engine


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE role_probe_should_fail (id int)",
        "ALTER TABLE users ADD COLUMN role_probe_should_fail int",
        "DROP TABLE users",
        "CREATE ROLE role_probe_should_fail",
        "CREATE DATABASE role_probe_should_fail",
    ],
)
def test_ddl_and_role_management_are_refused(app_engine, statement):
    # AUTOCOMMIT: CREATE DATABASE cannot run inside a transaction block, which would mask the privilege error.
    with app_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text(statement))
    assert getattr(excinfo.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE


def test_role_has_no_bypassrls(app_engine):
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")).scalar() is False


def test_dml_works_on_every_application_table(app_engine):
    with app_engine.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")
            )
        ]
        assert tables, "no application tables: run the migrations first"
        for table in tables:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                if table == "audit_log" and privilege in ("UPDATE", "DELETE"):
                    continue  # append-only (US6); covered by test_audit_append_only.py
                allowed = conn.execute(
                    text("SELECT has_table_privilege(current_user, :t, :p)"),
                    {"t": f'public."{table}"', "p": privilege},
                ).scalar()
                assert allowed, f"{privilege} missing on {table}"
        conn.execute(text("SELECT count(*) FROM users"))  # a real statement, not only a catalog check


def test_table_created_later_by_the_migration_role_is_usable_without_a_grant(app_engine):
    from app.core.config import migration_url, settings

    name = f"role_probe_{uuid.uuid4().hex[:8]}"
    owner_engine = create_engine(migration_url(settings))
    try:
        with owner_engine.begin() as conn:
            conn.execute(text(f"CREATE TABLE {name} (id serial PRIMARY KEY, v int)"))
        try:
            with app_engine.begin() as conn:
                conn.execute(text(f"INSERT INTO {name} (v) VALUES (1)"))
                assert conn.execute(text(f"SELECT v FROM {name}")).scalar() == 1
                conn.execute(text(f"UPDATE {name} SET v = 2"))
                conn.execute(text(f"DELETE FROM {name}"))
        finally:
            with owner_engine.begin() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
    finally:
        owner_engine.dispose()
