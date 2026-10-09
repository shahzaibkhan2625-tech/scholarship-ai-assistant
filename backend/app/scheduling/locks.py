"""Per-source mutual exclusion for source monitoring (002 FR-MON-6).

**Transaction-scoped, not session-scoped, on purpose.** The deployment's
`DATABASE_URL` is a Neon *pooled* (transaction-mode) endpoint, where a
session-scoped `pg_try_advisory_lock` can be taken on one backend connection
and silently "released"/lost on another, which would quietly weaken FR-MON-6.
`pg_try_advisory_xact_lock` is bound to the transaction instead: it is held
while that transaction is open (the pooler pins one backend for the duration
of an open transaction) and is released automatically at COMMIT or ROLLBACK,
whether or not the code ever reaches an explicit unlock.

The lock lives on its OWN connection/transaction, opened here, and that
transaction stays open for the whole per-source unit of work (fetch, change
detection, freshness refresh, stale sweep, counters). The unit of work itself
runs on the caller's session, whose existing helpers commit internally (001/
discovery behavior that must not change); that must not end the LOCK
transaction, which is why the lock cannot simply ride on the caller's session.

Keys are a stable signed int64 derived with `hashlib` (never Python's
per-process-randomized `hash()`), so every process computes the same key.
"""

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

__all__ = ["source_lock_key", "try_acquire_source_lock", "source_lock"]


def source_lock_key(source_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"source_monitor:{source_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def try_acquire_source_lock(connection: Connection, source_id: uuid.UUID) -> bool:
    """Non-blocking attempt on `connection`'s CURRENT transaction; the caller
    owns that transaction, and the lock is released when it commits/rolls back."""
    acquired = connection.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": source_lock_key(source_id)}
    ).scalar()
    return bool(acquired)


@contextmanager
def source_lock(db: Session, source_id: uuid.UUID) -> Iterator[bool]:
    """Yields True if this caller now holds the source's lock, False if another
    transaction does. Never blocks. The lock transaction is rolled back in a
    `finally`, so the lock is released even if the body raises."""
    bind = db.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield try_acquire_source_lock(connection, source_id)
        finally:
            transaction.rollback()
