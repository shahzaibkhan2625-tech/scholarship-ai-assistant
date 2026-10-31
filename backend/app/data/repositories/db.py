import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def safe_rollback(db: Session) -> None:
    """Roll the session back after a swallowed database error (010 FR-RECOV-1): a failed statement
    leaves the transaction aborted, so every later statement on the session fails until it ends.
    A rollback that itself fails (e.g. the connection is gone) is logged by type only and swallowed,
    so it can never mask the error being handled."""
    try:
        db.rollback()
    except Exception as exc:  # noqa: BLE001 - must not mask the original error
        logger.warning("session rollback failed: %s", type(exc).__name__)
