"""`cv_gen` workflow tests (T109): a fabricated claim injected into the draft
is blocked by `ground_check` and surfaces as a reported gap (not silently
removed); a Europass-requiring scholarship produces Europass-shaped output;
a successful generation populates `source_trace` for every claim. Runs
against the real DB (skip-if-unreachable, mirrors
tests/workflows/test_doc_pipeline.py); every row created is torn down. The
draft LLM call is mocked — never a real API call in this suite."""

import uuid
from unittest.mock import patch

import pytest

from app.data.repositories import document_repo
from app.models.application import Application
from app.models.document import ApplicationDocument, GeneratedDocument
from app.models.profile import EducationRecord, Profile
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from app.models.user import User
from app.workflows.cv_gen.graph import CvDraft, CvDraftClaim, run_cv_gen


@pytest.fixture
def cv_gen_fixture(db_session_factory):
    """User + profile (one grounded education fact) + scholarship + a
    durably persisted `applications` row, ready for `run_cv_gen`."""
    session = db_session_factory()

    user = User(email=f"cv-gen-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)

    profile = Profile(user_id=user.id, name="Ada Lovelace")
    session.add(profile)
    session.commit()
    session.refresh(profile)

    profile.education_records.append(
        EducationRecord(degree="BS", field="Computer Science", university="MIT", gpa=3.5, gpa_scale=4.0)
    )
    session.commit()
    session.refresh(profile)

    scholarship = Scholarship(
        name="CV Gen Test Scholarship", official_scholarship_url="https://example.test/cv-gen"
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)

    application = Application(user_id=user.id, scholarship_id=scholarship.id)
    session.add(application)
    session.commit()
    session.refresh(application)

    yield session, user, profile, scholarship, application

    session.query(GeneratedDocument).filter(GeneratedDocument.application_id == application.id).delete()
    session.query(ApplicationDocument).filter(ApplicationDocument.application_id == application.id).delete()
    session.query(Application).filter(Application.id == application.id).delete()
    session.query(Requirement).filter(Requirement.scholarship_id == scholarship.id).delete()
    session.query(EducationRecord).filter(EducationRecord.profile_id == profile.id).delete()
    session.query(Profile).filter(Profile.id == profile.id).delete()
    session.query(Scholarship).filter(Scholarship.id == scholarship.id).delete()
    session.query(User).filter(User.id == user.id).delete()
    session.commit()
    session.close()


def test_fabricated_claim_is_blocked_and_reported_as_gap_not_silently_removed(cv_gen_fixture) -> None:
    session, user, profile, scholarship, application = cv_gen_fixture

    fabricated_draft = CvDraft(
        claims=[
            CvDraftClaim(section="education", text="Completed a BS in Computer Science from MIT with GPA 3.5."),
            CvDraftClaim(section="awards", text="Won the Fulbright Award in 2020."),
        ]
    )

    with patch("app.workflows.cv_gen.graph._draft_cv", return_value=fabricated_draft):
        final_state = run_cv_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is True
    gaps = final_state.get("gaps") or []
    assert any("Fulbright" in (gap.get("claim") or "") for gap in gaps), gaps
    assert final_state.get("generated_document") is None

    rows = document_repo.list_generated_documents_for_application(session, user.id, application.id)
    assert rows == []


def test_europass_requiring_scholarship_produces_europass_shaped_output(cv_gen_fixture) -> None:
    session, user, profile, scholarship, application = cv_gen_fixture

    requirement = Requirement(
        scholarship_id=scholarship.id,
        category=RequirementCategory.OTHER,
        key="cv_format",
        value="Europass",
        mandatory=False,
        value_status="known",
        confidence="verified",
    )
    session.add(requirement)
    session.commit()
    session.expire(scholarship, ["requirements"])

    grounded_draft = CvDraft(
        claims=[CvDraftClaim(section="education", text="Completed a BS in Computer Science from MIT with GPA 3.5.")]
    )

    with patch("app.workflows.cv_gen.graph._draft_cv", return_value=grounded_draft):
        final_state = run_cv_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["format"] == "europass"
    assert final_state["blocked"] is False
    document = final_state["generated_document"]
    assert document is not None
    assert "europass" in (final_state.get("rendered_text") or "").lower()


def test_successful_generation_populates_source_trace_for_every_claim(cv_gen_fixture) -> None:
    session, user, profile, scholarship, application = cv_gen_fixture

    grounded_draft = CvDraft(
        claims=[
            CvDraftClaim(section="summary", text="Name: Ada Lovelace."),
            CvDraftClaim(section="education", text="Completed a BS in Computer Science from MIT with GPA 3.5."),
        ]
    )

    with patch("app.workflows.cv_gen.graph._draft_cv", return_value=grounded_draft):
        final_state = run_cv_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is False
    document = final_state["generated_document"]
    assert document is not None

    session.refresh(document)
    assert document.source_trace is not None
    assert len(document.source_trace) == len(grounded_draft.claims)
    for entry in document.source_trace:
        assert entry["claim"]
        assert entry["grounded_in"] and entry["grounded_in"] != "unknown"
        assert entry["grounded_in"].startswith("profile.")
