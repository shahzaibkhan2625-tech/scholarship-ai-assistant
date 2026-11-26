"""003 T256 (check-state half): check_state_repo CRUD and the history bootstrap (research R-08)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import check_state_repo as repo
from app.models.source import AccessMethod, FetchStatus, ReliabilityLevel, SourceFetchLog, SourceRegistry, SourceType

ZERO = "listing extraction yielded 0 candidates"


@pytest.fixture
def db(db_session_factory):
    with db_session_factory() as session:
        yield session


@pytest.fixture
def source(db):
    row = SourceRegistry(
        name="t",
        source_type=SourceType.GOV,
        official_status="official",
        domain=f"{uuid.uuid4().hex}.example",
        access_method=AccessMethod.WEB,
        reliability_level=ReliabilityLevel.HIGH,
    )
    db.add(row)
    db.flush()
    return row


def _log(db, source, status, error, minutes_ago):
    db.add(
        SourceFetchLog(
            source_id=source.id,
            status=status,
            error=error,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        )
    )
    db.flush()


def test_get_returns_none_when_no_row(db, source):
    assert repo.get(db, source.id) is None


def test_get_or_create_makes_a_row_with_defaults(db, source):
    state = repo.get_or_create(db, source.id)
    assert state.source_id == source.id
    assert (state.fingerprint, state.backoff_level, state.checks_window, state.changed_window) == (None, 0, 0, 0)
    assert state.validators_trusted is False and state.rules_review_needed is False
    assert repo.get_or_create(db, source.id).source_id == state.source_id  # idempotent


def test_update_changes_only_named_fields(db, source):
    repo.get_or_create(db, source.id)
    when = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    repo.update(db, source.id, fingerprint="a" * 64, fingerprint_at=when, last_extraction_outcome="ok")
    state = repo.get(db, source.id)
    assert (state.fingerprint, state.fingerprint_at, state.last_extraction_outcome) == ("a" * 64, when, "ok")
    assert state.backoff_level == 0
    assert state.updated_at is not None


def test_update_rejects_unknown_fields(db, source):
    repo.get_or_create(db, source.id)
    with pytest.raises(ValueError):
        repo.update(db, source.id, not_a_column=1)


def test_bootstrap_counts_consecutive_zero_candidate_rows(db, source):
    for i in range(5):
        _log(db, source, FetchStatus.OK, ZERO, minutes_ago=10 + i)
    assert repo.get_or_create(db, source.id).backoff_level == 5


def test_bootstrap_stops_at_the_first_other_row(db, source):
    _log(db, source, FetchStatus.OK, ZERO, minutes_ago=1)
    _log(db, source, FetchStatus.OK, ZERO, minutes_ago=2)
    _log(db, source, FetchStatus.OK, None, minutes_ago=3)  # a real extraction breaks the streak
    _log(db, source, FetchStatus.OK, ZERO, minutes_ago=4)
    assert repo.bootstrap_backoff_level(db, source.id) == 2


def test_bootstrap_with_136_rows_gives_the_cap(db, source):
    for i in range(136):
        _log(db, source, FetchStatus.OK, ZERO, minutes_ago=i + 1)
    assert repo.get_or_create(db, source.id).backoff_level == repo.MAX_BACKOFF_LEVEL


def test_source_with_no_history_starts_at_zero(db, source):
    assert repo.bootstrap_backoff_level(db, source.id) == 0
