"""003 T257: SourceFetchLogCreate.outcome (typed) and its persistence through log_fetch."""

import uuid

import pytest
from pydantic import ValidationError

from app.models.source import AccessMethod, FetchStatus, ReliabilityLevel, SourceRegistry, SourceType
from app.schemas.source import SourceFetchLogCreate


@pytest.mark.parametrize(
    "outcome", ["unchanged", "deferred", "rules", "provider_quota", "provider_error", "provider_parse", None]
)
def test_allowed_outcomes(outcome):
    row = SourceFetchLogCreate(source_id=uuid.uuid4(), status=FetchStatus.OK, outcome=outcome)
    assert row.outcome == outcome


def test_outcome_defaults_to_none():
    assert SourceFetchLogCreate(source_id=uuid.uuid4(), status=FetchStatus.OK).outcome is None


@pytest.mark.parametrize("outcome", ["bogus", "", "OK", "provider_"])
def test_unknown_outcome_is_rejected(outcome):
    with pytest.raises(ValidationError):
        SourceFetchLogCreate(source_id=uuid.uuid4(), status=FetchStatus.OK, outcome=outcome)


def _source(db):
    source = SourceRegistry(
        name="t",
        source_type=SourceType.GOV,
        official_status="official",
        domain=f"{uuid.uuid4().hex}.example",
        access_method=AccessMethod.WEB,
        reliability_level=ReliabilityLevel.HIGH,
    )
    db.add(source)
    db.flush()
    return source


def test_log_fetch_persists_outcome_and_defaults_to_none(db_session_factory):
    from app.data.repositories import source_repo

    with db_session_factory() as db:
        source = _source(db)
        plain = source_repo.log_fetch(db, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.OK))
        typed = source_repo.log_fetch(
            db, SourceFetchLogCreate(source_id=source.id, status=FetchStatus.FAIL, outcome="provider_quota")
        )
        assert plain.outcome is None
        assert typed.outcome == "provider_quota"
