"""Fetch-log retention (002 NFR-HARDEN-7, T228; ADR-0008, ADR-0011 decision 2).

Deletes `source_fetch_log` rows older than `FETCH_LOG_RETENTION_DAYS` EXCEPT three
floors, so the history that explains a source's current state is never pruned:

  (a) every row of each source's most recent `HEALTH_WINDOW_N` monitoring RUNS --
      ranked by the same `source_repo.ranked_monitoring_runs` helper health uses
      (runs, not rows: one check writes 2-3 rows);
  (b) each source's single most recent row of any kind (a source flagged `failing`
      by discovery's first-failure rule may have only an old NULL-run row);
  (c) each source's most recent OK row with `items_found` -- the extraction-cache /
      shrink-guard baseline read by `official_fetch` and the monitor graph.

Only `source_fetch_log` is touched. Pooler-safe: no locks, no session state, one
short transaction per batch. `source_ids` scopes the job (tests MUST pass it; the
shared database holds real rows); `None` means every source (production).

CLI (first production run -- count before deleting):
    python -m app.services.retention --dry-run [--source-id UUID ...]
"""

import argparse
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import all_, and_, bindparam, delete, func, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Session

from app.core.config import settings
from app.data.repositories import source_repo
from app.models.source import FetchStatus, SourceFetchLog

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_BATCH_SIZE", "main", "prune_fetch_log"]

DEFAULT_BATCH_SIZE = 1000


def _protected_ids(
    db: Session, *, cutoff: datetime, window_n: int, source_ids: list[uuid.UUID] | None
) -> list[uuid.UUID]:
    """Snapshot (computed once) of the ids of rows that must survive, restricted to
    the rows that would otherwise be prunable. Safe to reuse across batches: the
    floors only ever move forward, so a stale snapshot errs toward keeping rows."""

    def scoped(stmt):
        return stmt if source_ids is None else stmt.where(SourceFetchLog.source_id.in_(source_ids))

    ranked = source_repo.ranked_monitoring_runs(source_ids)
    floor_a = (
        select(SourceFetchLog.id)
        .join(
            ranked,
            and_(
                SourceFetchLog.source_id == ranked.c.source_id,
                SourceFetchLog.monitoring_run_id == ranked.c.monitoring_run_id,
            ),
        )
        .where(ranked.c.rn <= window_n, SourceFetchLog.started_at < cutoff)
    )
    floor_b = scoped(
        select(SourceFetchLog.id)
        .distinct(SourceFetchLog.source_id)
        .order_by(SourceFetchLog.source_id, SourceFetchLog.started_at.desc(), SourceFetchLog.id.desc())
    )
    floor_c = scoped(
        select(SourceFetchLog.id)
        .where(SourceFetchLog.status == FetchStatus.OK, SourceFetchLog.items_found.is_not(None))
        .distinct(SourceFetchLog.source_id)
        .order_by(SourceFetchLog.source_id, SourceFetchLog.started_at.desc(), SourceFetchLog.id.desc())
    )
    protected: set[uuid.UUID] = set()
    for stmt in (floor_a, floor_b, floor_c):
        protected.update(db.execute(stmt).scalars().all())
    return list(protected)


def prune_fetch_log(
    db: Session,
    *,
    retention_days: int | None = None,
    window_n: int | None = None,
    source_ids: Sequence[uuid.UUID] | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    now: datetime | None = None,
) -> int:
    """Returns the number of rows deleted (under `dry_run`: that WOULD be deleted).

    `source_ids=None` = every source; an EMPTY sequence = nothing (never "all")."""
    retention_days = settings.fetch_log_retention_days if retention_days is None else retention_days
    window_n = settings.health_window_n if window_n is None else window_n
    if retention_days < 1 or window_n < 1 or batch_size < 1:
        raise ValueError("retention_days, window_n and batch_size must each be >= 1")
    scope = None if source_ids is None else list(dict.fromkeys(source_ids))
    if scope is not None and not scope:
        return 0

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    protected = _protected_ids(db, cutoff=cutoff, window_n=window_n, source_ids=scope)

    prunable = [
        SourceFetchLog.started_at < cutoff,
        SourceFetchLog.id
        != all_(bindparam("protected", protected, type_=ARRAY(UUID(as_uuid=True)))),
    ]
    if scope is not None:
        prunable.append(SourceFetchLog.source_id.in_(scope))

    if dry_run:
        return db.execute(select(func.count()).select_from(SourceFetchLog).where(*prunable)).scalar_one()

    total = batches = 0
    while True:
        batch = (
            select(SourceFetchLog.id)
            .where(*prunable)
            .order_by(SourceFetchLog.started_at, SourceFetchLog.id)
            .limit(batch_size)
        )
        deleted = db.execute(delete(SourceFetchLog).where(SourceFetchLog.id.in_(batch))).rowcount
        db.commit()  # one short transaction per batch; progress survives a later failure
        total += deleted
        batches += 1
        if deleted < batch_size:
            break
    logger.info(
        "fetch-log retention deleted %s row(s) in %s batch(es) (cutoff %s, scope %s)",
        total,
        batches,
        cutoff.isoformat(),
        "all sources" if scope is None else f"{len(scope)} source(s)",
    )
    return total


def _uuid_arg(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a valid UUID: {value!r}") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune old source_fetch_log rows (floors keep health history).")
    parser.add_argument("--dry-run", action="store_true", help="count what would be deleted; delete nothing")
    parser.add_argument(
        "--source-id", action="append", type=_uuid_arg, dest="source_ids", metavar="UUID",
        help="limit to this source (repeatable); default: every source",
    )
    args = parser.parse_args(argv)

    from app.data.repositories.db import SessionLocal

    with SessionLocal() as db:
        count = prune_fetch_log(db, source_ids=args.source_ids, dry_run=args.dry_run)
    print(f"dry run: {count} row(s) would be deleted" if args.dry_run else f"deleted {count} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
