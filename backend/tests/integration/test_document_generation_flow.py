"""Integration test covering spec.md US5 Acceptance Scenarios 1-5 end to end,
driven through the real HTTP API (mirrors `tests/integration/test_qa_flow.py`).
The LLM boundary is mocked at exactly the points `tests/api/test_documents_api.py`
and `tests/workflows/test_cv_gen.py`/`test_sop_gen.py` already mock it
(`extract_document_fields`, `_draft_cv`, `_draft_sop`, `_polish_sop`) so this
suite is deterministic and never calls the real (quota-limited) Gemini free
tier."""

import uuid
from unittest.mock import patch

import pymupdf
import pytest

from app.models.application import Application
from app.models.document import ApplicationDocument, GeneratedDocument
from app.models.profile import EducationRecord, Profile
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from app.models.user import User
from app.data.files.storage import get_storage
from app.tools.extract_document_fields import ExtractedDocumentFields
from app.workflows.cv_gen.graph import CvDraft, CvDraftClaim
from app.workflows.sop_gen.graph import SopDraft, SopDraftClaim


def _text_pdf_bytes(text: str = "Sample transcript text") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _delete_applications_and_children(session, application_ids: list[uuid.UUID]) -> None:
    if not application_ids:
        return
    session.query(ApplicationDocument).filter(ApplicationDocument.application_id.in_(application_ids)).delete(
        synchronize_session=False
    )
    session.query(GeneratedDocument).filter(GeneratedDocument.application_id.in_(application_ids)).delete(
        synchronize_session=False
    )
    session.query(Application).filter(Application.id.in_(application_ids)).delete(synchronize_session=False)
    session.commit()


@pytest.fixture
def scholarship_with_requirements(db_session_factory):
    """A scholarship requiring a minimum GPA (US5 Scenario 1/2), a Europass
    CV format, and specific SOP questions (Scenario 3)."""
    session = db_session_factory()
    scholarship = Scholarship(
        name="US5 Flow Test Scholarship", official_scholarship_url="https://example.test/us5-flow"
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)

    session.add_all(
        [
            Requirement(
                scholarship_id=scholarship.id,
                category=RequirementCategory.GPA,
                key="min_gpa",
                value=3.0,
                mandatory=True,
                value_status="known",
                confidence="verified",
            ),
            Requirement(
                scholarship_id=scholarship.id,
                category=RequirementCategory.OTHER,
                key="cv_format",
                value="Europass",
                mandatory=False,
                value_status="known",
                confidence="verified",
            ),
            Requirement(
                scholarship_id=scholarship.id,
                category=RequirementCategory.OTHER,
                key="sop_questions",
                value=["Why do you want to study this program?"],
                mandatory=True,
                value_status="known",
                confidence="verified",
            ),
        ]
    )
    session.commit()
    scholarship_id = scholarship.id
    session.close()

    yield scholarship_id

    session = db_session_factory()
    orphaned_ids = [
        app_id for (app_id,) in session.query(Application.id).filter(Application.scholarship_id == scholarship_id)
    ]
    _delete_applications_and_children(session, orphaned_ids)
    session.query(Requirement).filter(Requirement.scholarship_id == scholarship_id).delete()
    existing = session.get(Scholarship, scholarship_id)
    if existing is not None:
        session.delete(existing)
        session.commit()
    session.close()


@pytest.fixture
def application_for(db_session_factory, scholarship_with_requirements):
    created_ids: list[uuid.UUID] = []

    def _create(user_id: uuid.UUID) -> uuid.UUID:
        session = db_session_factory()
        row = Application(user_id=user_id, scholarship_id=scholarship_with_requirements)
        session.add(row)
        session.commit()
        session.refresh(row)
        application_id = row.id
        session.close()
        created_ids.append(application_id)
        return application_id

    yield _create

    session = db_session_factory()
    _delete_applications_and_children(session, created_ids)
    session.close()


def _add_profile_with_facts(db_session_factory, user_id: uuid.UUID, *, gpa: float = 3.5) -> None:
    """A real profile fact for the generation scenarios — mirrors
    `tests/api/test_generation_api.py::profile_with_facts`."""
    session = db_session_factory()
    profile = Profile(user_id=user_id, name="Flow Test Applicant")
    session.add(profile)
    session.commit()
    session.refresh(profile)
    session.add(EducationRecord(profile_id=profile.id, degree="BS", field="Computer Science", university="MIT", gpa=gpa, gpa_scale=4.0))
    session.commit()
    session.close()


def _cleanup_profile(db_session_factory, user_id: uuid.UUID) -> None:
    session = db_session_factory()
    profile = session.query(Profile).filter(Profile.user_id == user_id).one_or_none()
    if profile is not None:
        session.query(EducationRecord).filter(EducationRecord.profile_id == profile.id).delete()
        session.query(Profile).filter(Profile.id == profile.id).delete()
        session.commit()
    session.close()


def test_scenario_1_transcript_parsed_associated_and_requirement_satisfied(
    authed_user, application_for, db_session_factory
) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    extracted = ExtractedDocumentFields(gpa=3.5, gpa_scale=4.0, value_status="known")
    with patch("app.workflows.doc_pipeline.graph.extract_document_fields", return_value=extracted):
        response = client.post(
            f"/applications/{application_id}/documents",
            headers=headers,
            files={"file": ("transcript.pdf", _text_pdf_bytes(), "application/pdf")},
            data={"type": "transcript"},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["parsed_meta"]["gpa"] == 3.5
    assert body["satisfies_requirement_id"] is not None, "the GPA requirement must be marked satisfied"


def test_scenario_2_gpa_inconsistency_flagged_not_silently_resolved(
    authed_user, application_for, db_session_factory
) -> None:
    """A document GPA that disagrees with the profile's own GPA is flagged,
    never silently overwritten or dropped."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])
    _add_profile_with_facts(db_session_factory, authed_user["user_id"], gpa=3.9)

    extracted = ExtractedDocumentFields(gpa=3.2, gpa_scale=4.0, value_status="known")
    try:
        with patch("app.workflows.doc_pipeline.graph.extract_document_fields", return_value=extracted):
            response = client.post(
                f"/applications/{application_id}/documents",
                headers=headers,
                files={"file": ("transcript.pdf", _text_pdf_bytes(), "application/pdf")},
                data={"type": "transcript"},
            )

        assert response.status_code == 201
        body = response.json()
        assert body["inconsistency_flags"] is not None
        mismatch = next(f for f in body["inconsistency_flags"] if f.get("field") == "gpa")
        assert mismatch["profile_value"] == 3.9
        assert mismatch["document_value"] == 3.2

        session = db_session_factory()
        profile = session.query(Profile).filter(Profile.user_id == authed_user["user_id"]).one()
        assert float(profile.education_records[0].gpa) == 3.9  # untouched, never silently overwritten
        session.close()
    finally:
        _cleanup_profile(db_session_factory, authed_user["user_id"])


def test_scenario_3_format_and_specified_questions_are_honored(
    authed_user, application_for, db_session_factory
) -> None:
    """Europass CV format and the scholarship's own SOP question are honored
    rather than a generic default."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])
    _add_profile_with_facts(db_session_factory, authed_user["user_id"])

    cv_draft = CvDraft(claims=[CvDraftClaim(section="education", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0.")])
    sop_draft = SopDraft(
        claims=[SopDraftClaim(question="Why do you want to study this program?", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0.")]
    )

    try:
        with patch("app.workflows.cv_gen.graph._draft_cv", return_value=cv_draft):
            cv_response = client.post(f"/applications/{application_id}/generate/cv", headers=headers)
        assert cv_response.status_code == 200
        rendered_cv = get_storage().get(cv_response.json()["file_ref"]).decode("utf-8")
        assert "europass" in rendered_cv.lower(), rendered_cv

        with (
            patch("app.workflows.sop_gen.graph._draft_sop", return_value=sop_draft),
            patch("app.workflows.sop_gen.graph._polish_sop", return_value=sop_draft.claims),
        ):
            sop_response = client.post(f"/applications/{application_id}/generate/sop", headers=headers)
        assert sop_response.status_code == 200
        sop_trace = sop_response.json()["source_trace"]
        assert any(entry.get("question") == "Why do you want to study this program?" for entry in sop_trace), sop_trace
    finally:
        _cleanup_profile(db_session_factory, authed_user["user_id"])


def test_scenario_4_every_generated_claim_is_traceable(authed_user, application_for, db_session_factory) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])
    _add_profile_with_facts(db_session_factory, authed_user["user_id"])

    cv_draft = CvDraft(
        claims=[
            CvDraftClaim(section="summary", text="Name: Flow Test Applicant."),
            CvDraftClaim(section="education", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0."),
        ]
    )

    try:
        with patch("app.workflows.cv_gen.graph._draft_cv", return_value=cv_draft):
            response = client.post(f"/applications/{application_id}/generate/cv", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["source_trace"]
        assert len(body["source_trace"]) == len(cv_draft.claims)
        for entry in body["source_trace"]:
            assert entry["claim"]
            assert entry["grounded_in"]
    finally:
        _cleanup_profile(db_session_factory, authed_user["user_id"])


@pytest.fixture
def bare_scholarship_application_for(db_session_factory):
    """A scholarship with ZERO requirements, unlike `scholarship_with_requirements`
    — `_requirement_facts` treats every known-status requirement as an allowed
    fact regardless of category (BP §16's "scholarship's verified
    requirements" pool), so a scholarship carrying requirements always gives
    generation *something* to draft from. This scenario needs a genuinely
    empty allowed-facts pool (no profile facts, no requirement facts) so the
    "no facts" gap fires deterministically without ever calling the LLM."""
    session = db_session_factory()
    scholarship = Scholarship(name="US5 Flow Gap Test Scholarship", official_scholarship_url="https://example.test/us5-flow-gap")
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)
    scholarship_id = scholarship.id
    session.close()

    created_ids: list[uuid.UUID] = []

    def _create(user_id: uuid.UUID) -> uuid.UUID:
        session = db_session_factory()
        row = Application(user_id=user_id, scholarship_id=scholarship_id)
        session.add(row)
        session.commit()
        session.refresh(row)
        application_id = row.id
        session.close()
        created_ids.append(application_id)
        return application_id

    yield _create

    session = db_session_factory()
    _delete_applications_and_children(session, created_ids)
    session.query(Scholarship).filter(Scholarship.id == scholarship_id).delete()
    session.commit()
    session.close()


def test_scenario_5_gap_reported_not_invented(authed_user, bare_scholarship_application_for) -> None:
    """The user's profile has no facts, no documents, and the scholarship
    declares no requirements — generation must report the gap (409) rather
    than invent content to fill it. No profile fixture is added here,
    deliberately, and the empty facts pool means `cv_gen`'s own "nothing to
    draft from" gate fires before any LLM call, so this stays deterministic
    and quota-free."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = bare_scholarship_application_for(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/generate/cv", headers=headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.get("gaps")
