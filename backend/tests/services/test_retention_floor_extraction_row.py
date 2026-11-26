"""003 T453 (FR-SKIP-8, SC-012): which row does retention floor (c) protect once ingestion has written
its own per-record success rows (`items_found=1`, no fetched URL)?

Recorded answer before the fix: the NEWEST OK row with `items_found`, i.e. an ingestion row, so the
older extraction row (the one the discovery cache and the baseline read) could be pruned. The fix keeps
floor (c) as it was and adds the extraction row (OK, `items_found`, a fetched URL) as its own floor."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.data.repositories import source_repo
from app.models.source import FetchStatus, SourceFetchLog
from app.schemas.source import SourceRegistryCreate
from app.services import retention

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(db_session_factory):
    with db_session_factory() as session:
        yield session


def _source(db):
    return source_repo.upsert_source_by_domain(
        db,
        SourceRegistryCreate(
            name="Retention Floor", source_type="gov", official_status="official",
            domain=f"floor-{uuid.uuid4().hex[:10]}.example.com", access_method="web",
            discovery_role=True, reliability_level="high", status="active",
        ),
    )


def _row(db, source, age_days, *, status=FetchStatus.OK, items_found=None, fetched_url=None) -> uuid.UUID:
    row = SourceFetchLog(
        source_id=source.id, started_at=NOW - timedelta(days=age_days), status=status,
        items_found=items_found, fetched_url=fetched_url,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.id


def _remaining(db, source) -> set[uuid.UUID]:
    return set(db.execute(select(SourceFetchLog.id).where(SourceFetchLog.source_id == source.id)).scalars())


def test_the_extraction_row_survives_when_newer_ingestion_rows_exist(db):
    source = _source(db)
    extraction = _row(db, source, 300, items_found=5, fetched_url="https://x.example.com/list")
    ingestion = [_row(db, source, age, items_found=1) for age in (250, 200, 150)]  # no fetched URL
    latest = _row(db, source, 100, status=FetchStatus.FAIL)  # floor (b)
    retention.prune_fetch_log(db, retention_days=90, window_n=5, source_ids=[source.id], now=NOW)
    remaining = _remaining(db, source)
    assert extraction in remaining, "the extraction row must be protected, not only the newest items_found row"
    assert latest in remaining
    assert ingestion[-1] in remaining  # floor (c) as it was: the newest OK row with items_found is unchanged
    assert not ({ingestion[0], ingestion[1]} & remaining)


def test_floor_c_behaviour_without_ingestion_rows_is_unchanged(db):
    source = _source(db)
    older = _row(db, source, 320, items_found=9, fetched_url="https://x.example.com/list")
    newer = _row(db, source, 300, items_found=7, fetched_url="https://x.example.com/list")
    latest = _row(db, source, 100, status=FetchStatus.TIMEOUT)
    retention.prune_fetch_log(db, retention_days=90, window_n=5, source_ids=[source.id], now=NOW)
    assert _remaining(db, source) == {newer, latest}
    assert older not in _remaining(db, source)
