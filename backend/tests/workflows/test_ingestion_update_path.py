"""D1 (ADR-0006): `run_ingestion(existing_scholarship_id=...)` — the monitoring
update path — and proof the default call is unchanged. Real (rolled-back) DB;
LLM/embedding mocked."""

import inspect
import uuid
from datetime import date

import pytest

from app.data.repositories import scholarship_repo, source_repo
from app.models.scholarship import DegreeLevel, FundingStatus, Scholarship, VerificationStatus
from app.models.source import ScholarshipSource
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.workflows.ingestion.graph import run_ingestion

_FAKE = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred", funding_status="fully_funded",
    funding_status_confidence="inferred", provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred", field="CS", field_confidence="inferred",
)


@pytest.fixture
def ctx(db_session_factory, monkeypatch):
    monkeypatch.setattr("app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: _FAKE)
    session = db_session_factory()
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Update Path Test", source_type="gov", official_status="official",
            domain=f"upd-{uuid.uuid4().hex[:10]}.example.com", access_method="web", reliability_level="high",
            status="active",
        ),
    )
    name = f"Update Path Scholarship {uuid.uuid4().hex[:8]}"
    created = run_ingestion(
        session, source_id=source.id, source_url=f"https://{source.domain}",
        raw={"name": name, "degree_level": "PhD", "funding_status": "fully_funded", "deadline": "2027-03-01"},
    )["scholarship"]
    try:
        yield session, source, name, created
    finally:
        session.close()


def _update(session, source, name, scholarship_id, **raw):
    return run_ingestion(
        session, source_id=source.id, source_url=f"https://{source.domain}", raw={"name": name, **raw},
        existing_scholarship_id=scholarship_id, field_conflicts={"deadline": []},
    )


def test_keyword_is_optional_and_defaults_to_none():
    assert inspect.signature(run_ingestion).parameters["existing_scholarship_id"].default is None


def test_update_writes_new_values_in_place_with_prior_audit_and_no_extra_rows(ctx):
    session, source, name, created = ctx
    created.verification_status = VerificationStatus.VERIFIED
    session.commit()
    rows_before = session.query(Scholarship).filter(Scholarship.name == name).count()
    sources_before = session.query(ScholarshipSource).filter(ScholarshipSource.scholarship_id == created.id).count()

    state = _update(session, source, name, created.id, deadline="2027-09-15", funding_status="tuition_only")

    assert state.get("error") is None and state["scholarship"].id == created.id
    session.refresh(created)
    assert created.deadline == date(2027, 9, 15) and created.funding_status == FundingStatus.TUITION_ONLY
    assert created.degree_level == DegreeLevel.PHD  # untouched
    assert created.verification_status == VerificationStatus.VERIFIED  # an update never rewrites verification
    assert session.query(Scholarship).filter(Scholarship.name == name).count() == rows_before
    assert (
        session.query(ScholarshipSource).filter(ScholarshipSource.scholarship_id == created.id).count()
        == sources_before
    )
    prior = {r.key: r for r in scholarship_repo.get_field_rows(session, created.id) if r.key.startswith("_prior:")}
    assert set(prior) == {"_prior:deadline", "_prior:funding_status"}
    assert prior["_prior:deadline"].value["value"] == "2027-03-01"
    assert prior["_prior:funding_status"].value["value"] == "fully_funded"
    assert prior["_prior:deadline"].confidence == "verified"
    # derive_conflicts / conflict_resolution are skipped on this path, even when a caller supplies field_conflicts
    assert not [r for r in scholarship_repo.get_field_rows(session, created.id) if r.value_status == "conflicting"]


def test_update_with_nothing_different_writes_nothing(ctx):
    session, source, name, created = ctx
    before = len(scholarship_repo.get_field_rows(session, created.id))
    state = _update(session, source, name, created.id, deadline="2027-03-01", degree_level="PhD")
    assert state.get("error") is None
    assert len(scholarship_repo.get_field_rows(session, created.id)) == before


def test_update_never_degrades_to_unknown_or_unparseable_values(ctx):
    session, source, name, created = ctx
    _update(session, source, name, created.id, deadline="soon", funding_status="unknown", degree_level="wizard")
    session.refresh(created)
    assert created.deadline == date(2027, 3, 1) and created.funding_status == FundingStatus.FULLY_FUNDED
    assert created.degree_level == DegreeLevel.PHD


def test_update_for_a_missing_scholarship_is_a_logged_store_failure_not_a_new_row(ctx):
    session, source, name, _created = ctx
    state = _update(session, source, name, uuid.uuid4(), deadline="2027-09-15")
    assert state["error_stage"] == "store" and state.get("scholarship") is None
    assert any(
        log.status == "fail" and "store" in (log.error or "")
        for log in source_repo.get_fetch_logs_for_source(session, source.id)
    )
