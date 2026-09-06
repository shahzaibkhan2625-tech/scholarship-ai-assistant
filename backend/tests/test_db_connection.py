import pytest
from sqlalchemy import text


def test_db_connection() -> None:
    try:
        from app.data.repositories.db import SessionLocal
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping DB connection test.")
        return

    with SessionLocal() as session:
        result = session.execute(text("SELECT 1"))
        assert result.scalar() == 1
