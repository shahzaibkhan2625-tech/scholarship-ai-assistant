"""The audit log is append-only by privilege (010 US6, FR-AUDIT-3, SC-006).

Connected as `scholarship_app` (CI, or the local throw-away container after the role script),
INSERT and SELECT on `audit_log` work and UPDATE, DELETE and TRUNCATE are refused. Connected as an
owner-level role these tests skip with a stated reason: the owner may do anything, so they would
prove nothing."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from tests.db_guard import require_db_allowed

OWNER_SKIP = "connected role is owner-level; run with DATABASE_URL set to the scholarship_app role"
INSUFFICIENT_PRIVILEGE = "42501"


@pytest.fixture(scope="module")
def app_engine():
    require_db_allowed()
    try:
        from app.data.repositories.db import engine
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping DB-dependent tests.")
    try:
        with engine.connect() as conn:
            owner = conn.execute(
                text(
                    "SELECT r.rolsuper OR pg_has_role(current_user, c.relowner, 'USAGE') "
                    "FROM pg_class c, pg_roles r WHERE r.rolname = current_user "
                    "AND c.relname = 'audit_log' AND c.relkind = 'r' AND c.relnamespace = 'public'::regnamespace"
                )
            ).scalar_one_or_none()
    except OperationalError:
        pytest.skip("Could not reach the configured database; skipping DB-dependent tests.")
    if owner is None:
        pytest.skip("audit_log does not exist; run alembic upgrade head first")
    if owner:
        pytest.skip(OWNER_SKIP)
    return engine


@pytest.fixture
def conn(app_engine):
    # Every statement runs in a transaction that is rolled back, so a successful INSERT leaves no row.
    with app_engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


def _insert(conn) -> uuid.UUID:
    row_id = uuid.uuid4()
    conn.execute(
        text("INSERT INTO audit_log (id, user_id, action, entity) VALUES (:id, :uid, 'user.signup', :entity)"),
        {"id": row_id, "uid": uuid.uuid4(), "entity": "user:probe"},
    )
    return row_id


def test_insert_and_select_work(conn):
    row_id = _insert(conn)
    assert conn.execute(text("SELECT count(*) FROM audit_log WHERE id = :id"), {"id": row_id}).scalar_one() == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'tampered'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
)
def test_update_delete_and_truncate_are_refused(conn, statement):
    with pytest.raises(DBAPIError) as excinfo:
        conn.execute(text(statement))
    assert getattr(excinfo.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
