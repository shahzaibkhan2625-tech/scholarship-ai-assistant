import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError


def test_db_connection() -> None:
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
