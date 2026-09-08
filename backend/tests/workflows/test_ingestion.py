"""`ingestion` workflow tests (T087, Blueprint §10, §31): a node failure is
logged to source_fetch_log and the record is never silently dropped; a
clean fixture record flows normalize->classify->dedup->verify->store->index
and is retrievable afterward with correct value_status/confidence/
lifecycle_status. Runs against the real DB (skip-if-unreachable, mirrors
tests/sources/test_connectors.py); every row created is torn down. All
network/LLM/embedding calls are mocked.
"""

import uuid
from unittest.mock import patch

import pytest

from app.data.repositories import source_repo
from app.models.scholarship import LifecycleStatus, Scholarship, ScholarshipField
from app.models.source import ScholarshipSource, SourceFetchLog, SourceRegistry
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.services.conflict_resolution import ConflictResolution
from app.services.dedup import DedupCandidate
from app.workflows.ingestion.graph import run_ingestion


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    created_sources: list[uuid.UUID] = []
    created_scholarships: list[uuid.UUID] = []
    try:
        yield session, created_sources, created_scholarships
    finally:
        for scholarship_id in created_scholarships:
            session.query(ScholarshipSource).filter(ScholarshipSource.scholarship_id == scholarship_id).delete()
            row = session.get(Scholarship, scholarship_id)
            if row is not None:
                session.delete(row)
        session.commit()
        for source_id in created_sources:
            session.query(SourceFetchLog).filter(SourceFetchLog.source_id == source_id).delete()
            row = session.get(SourceRegistry, source_id)
            if row is not None:
                session.delete(row)
        session.commit()
        session.close()


def _active_source(session, created_sources) -> SourceRegistry:
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Ingestion Test Source",
            source_type="gov",
            official_status="official",
            domain=f"ingestion-test-{uuid.uuid4().hex}.example.com",
            access_method="web",
            reliability_level="high",
            status="active",
        ),
    )
    created_sources.append(source.id)
    return source


_FAKE_CLASSIFICATION = ClassifiedScholarshipFields(
    degree_level="PhD",
    degree_level_confidence="inferred",  # classify never emits "verified" (§14/§32) — reserved for official confirmation
    funding_status="fully_funded",
    funding_status_confidence="inferred",
    provider_type="gov",
    provider_type_confidence="inferred",
    country="Germany",
    country_confidence="inferred",
    field="Computer Science",
    field_confidence="inferred",
)


def test_node_failure_logs_fail_entry_and_never_silently_drops_the_record(db):
    session, created_sources, created_scholarships = db
    source = _active_source(session, created_sources)

    raw = {"name": "Failure Case Scholarship", "country": "Germany", "deadline": "2027-01-01"}

    with patch(
        "app.workflows.ingestion.graph.classify_service.classify_candidate",
        side_effect=RuntimeError("classification backend unavailable"),
    ):
        result = run_ingestion(session, source_id=source.id, source_url="https://example.test/failure-case", raw=raw)

    assert result.get("error") is not None
    assert result.get("error_stage") == "classify"
    assert "scholarship" not in result or result.get("scholarship") is None

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    assert any(log.status == "fail" and "classify" in (log.error or "") for log in logs)

    # Never silently dropped: the failure is explicitly flagged in both the
    # returned state and the fetch log — there is no code path where this
    # record simply vanishes with no trace.


def test_happy_path_is_retrievable_with_correct_status_confidence_lifecycle(db):
    session, created_sources, created_scholarships = db
    source = _active_source(session, created_sources)

    raw = {
        "name": "Clean Fixture Scholarship",
        "provider": "Federal Ministry of Education",
        "country": "Germany",
        "field": "Computer Science",
        "degree_level": "PhD",
        "funding_status": "fully_funded",
        "deadline": "2027-06-01",
        "application_fee": "none",
        "language_requirement": "IELTS 6.5",
    }

    with patch("app.workflows.ingestion.graph.classify_service.classify_candidate", return_value=_FAKE_CLASSIFICATION):
        result = run_ingestion(
            session,
            source_id=source.id,
            source_url="https://example.test/clean-fixture",
            raw=raw,
            official_source_confirmed=True,
        )

    assert result.get("error") is None
    scholarship = result["scholarship"]
    created_scholarships.append(scholarship.id)

    reloaded = session.get(Scholarship, scholarship.id)
    assert reloaded is not None
    assert reloaded.name == "Clean Fixture Scholarship"
    assert reloaded.degree_level == "PhD"
    assert reloaded.country == "Germany"
    assert reloaded.lifecycle_status == LifecycleStatus.VERIFIED  # official_source_confirmed=True, freshly retrieved
    assert reloaded.application_fee == "none"  # first-class Scholarship column

    field_rows = session.query(ScholarshipField).filter(ScholarshipField.scholarship_id == scholarship.id).all()
    language_field = next(f for f in field_rows if f.key == "language_requirement")
    assert language_field.value == "IELTS 6.5"
    assert language_field.value_status == "known"
    assert language_field.confidence == "inferred"

    provider_type_field = next(f for f in field_rows if f.key == "provider_type")
    assert provider_type_field.value == "gov"
    assert provider_type_field.confidence == "inferred"

    sources = session.query(ScholarshipSource).filter(ScholarshipSource.scholarship_id == scholarship.id).all()
    assert len(sources) == 1
    assert sources[0].verification_status == "verified"


def test_missing_required_field_is_flagged_not_silently_dropped(db):
    session, created_sources, created_scholarships = db
    source = _active_source(session, created_sources)

    raw = {"country": "Germany"}  # no 'name' -> required-field validation failure

    with patch("app.workflows.ingestion.graph.classify_service.classify_candidate", return_value=_FAKE_CLASSIFICATION):
        result = run_ingestion(session, source_id=source.id, source_url="https://example.test/missing-name", raw=raw)

    assert result.get("error") is not None
    assert result.get("error_stage") == "store"
    assert result.get("retryable") is False  # data-validation failure, not retried

    logs = source_repo.get_fetch_logs_for_source(session, source.id)
    assert any(log.status == "fail" and "store" in (log.error or "") for log in logs)


def test_dedup_match_with_differing_fields_auto_derives_conflict_without_caller_supplying_it(db):
    """Blueprint §32: when dedup finds an existing (name+university+intake)
    match whose stored field values disagree with the new record's, the
    workflow must derive field_conflicts itself and invoke
    conflict_resolution — the caller here never passes field_conflicts."""

    session, created_sources, created_scholarships = db
    source = _active_source(session, created_sources)

    raw = {
        "name": "Auto Conflict Scholarship",
        "university": "Test University",
        "intake": "Fall 2027",
        "country": "Germany",
        "funding_status": "fully_funded",
        "deadline": "2027-06-01",
    }

    existing_candidates = [
        DedupCandidate(id="existing-1", name=raw["name"], university=raw["university"], intake=raw["intake"]),
    ]
    existing_records = {
        "existing-1": {
            "fields": {"funding_status": "partially_funded"},  # disagrees with the new record's fully_funded
            "source_id": "existing-source",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "is_official": True,
            "reliability": "high",
        },
    }

    fake_resolution = ConflictResolution(
        value=None, value_status="conflicting", resolved_by=None, winning_source_id=None, conflicting_values=[]
    )

    with (
        patch("app.workflows.ingestion.graph.classify_service.classify_candidate", return_value=_FAKE_CLASSIFICATION),
        patch(
            "app.workflows.ingestion.graph.conflict_service.resolve_conflict", return_value=fake_resolution
        ) as mock_resolve,
    ):
        result = run_ingestion(
            session,
            source_id=source.id,
            source_url="https://example.test/auto-conflict",
            raw=raw,
            existing_candidates=existing_candidates,
            existing_records=existing_records,
            official_source_confirmed=True,
        )

    # No field_conflicts were ever supplied by the caller -- this must have
    # been derived purely from the dedup match + differing stored value.
    mock_resolve.assert_called_once()
    called_values = {c.value for c in mock_resolve.call_args.args[0]}
    assert called_values == {"partially_funded", "fully_funded"}

    assert result.get("error") is None
    scholarship = result["scholarship"]
    created_scholarships.append(scholarship.id)

    conflict_field = (
        session.query(ScholarshipField)
        .filter(ScholarshipField.scholarship_id == scholarship.id, ScholarshipField.key == "funding_status")
        .one()
    )
    assert conflict_field.value_status == "conflicting"


def test_missing_soft_required_fields_are_stored_but_flagged_incomplete(db):
    """Blueprint §31: degree_level, country, deadline-or-status, and at least
    one official source are required before STORE, but (unlike `name`) are
    DB-nullable -- a record missing one must still be stored (never dropped)
    with an explicit incomplete/flagged marker, not treated as a normal,
    complete, published record."""

    session, created_sources, created_scholarships = db
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Non-Official Aggregator Source",
            source_type="approved_aggregator",
            official_status="aggregator",
            domain=f"non-official-{uuid.uuid4().hex}.example.com",
            access_method="web",
            reliability_level="medium",
            status="active",
        ),
    )
    created_sources.append(source.id)

    raw = {"name": "Incomplete Fixture Scholarship"}  # no degree_level, country, deadline/closing_status

    empty_classification = ClassifiedScholarshipFields(
        degree_level=None,
        degree_level_confidence="unknown",
        funding_status=None,
        funding_status_confidence="unknown",
        provider_type=None,
        provider_type_confidence="unknown",
        country=None,
        country_confidence="unknown",
        field=None,
        field_confidence="unknown",
    )

    with patch("app.workflows.ingestion.graph.classify_service.classify_candidate", return_value=empty_classification):
        result = run_ingestion(
            session, source_id=source.id, source_url="https://example.test/incomplete-fixture", raw=raw
        )

    assert result.get("error") is None
    scholarship = result["scholarship"]
    created_scholarships.append(scholarship.id)

    expected_missing = {"degree_level", "country", "deadline_or_status", "official_source"}
    assert set(result["incomplete_fields"]) == expected_missing

    completeness_field = (
        session.query(ScholarshipField)
        .filter(ScholarshipField.scholarship_id == scholarship.id, ScholarshipField.key == "_record_completeness")
        .one()
    )
    assert completeness_field.value["status"] == "incomplete"
    assert set(completeness_field.value["missing_fields"]) == expected_missing
    assert completeness_field.value_status == "unknown"
