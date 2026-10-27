import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from tests.db_guard import require_db_allowed


def test_db_connection() -> None:
    require_db_allowed()
    try:
        from app.data.repositories.db import SessionLocal
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping DB connection test.")
        return

    try:
        with SessionLocal() as session:
            result = session.execute(text("SELECT 1"))
            assert result.scalar() == 1
    except OperationalError:
        pytest.skip("Could not reach the configured database; skipping DB connection test.")
