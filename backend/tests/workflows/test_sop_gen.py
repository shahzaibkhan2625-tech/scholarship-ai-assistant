"""`sop_gen` workflow tests (T110): mirrors `tests/workflows/test_cv_gen.py`'s
conventions — real DB (skip-if-unreachable), every row created is torn down,
the LLM draft/polish calls are mocked (never a real API call in this suite).

Added in this slice specifically to cover the workflow's own logic
deterministically: the live-network contract tests in
`tests/api/test_generation_api.py` exercise the real Gemini free-tier API,
whose daily quota (20 requests/day for `gemini-2.5-flash`) is easily
exhausted during iteration on prompt wording — these tests give durable,
quota-free coverage of the same grounding/outline/polish-gate behavior."""

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
from app.workflows.sop_gen.graph import SopDraft, SopDraftClaim, _extract_sop_questions, run_sop_gen


@pytest.fixture
def sop_gen_fixture(db_session_factory):
    """User + profile (one grounded education fact) + scholarship + a
    durably persisted `applications` row, ready for `run_sop_gen`."""
    session = db_session_factory()

    user = User(email=f"sop-gen-{uuid.uuid4().hex}@example.com", password_hash="x")
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
        name="SOP Gen Test Scholarship", official_scholarship_url="https://example.test/sop-gen"
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


def test_fabricated_claim_is_blocked_and_reported_as_gap_not_silently_removed(sop_gen_fixture) -> None:
    session, user, profile, scholarship, application = sop_gen_fixture

    fabricated_draft = SopDraft(
        claims=[
            SopDraftClaim(question="Personal Statement", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0."),
            SopDraftClaim(question="Personal Statement", text="I won the Fulbright Award in 2020."),
        ]
    )

    with patch("app.workflows.sop_gen.graph._draft_sop", return_value=fabricated_draft):
        final_state = run_sop_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is True
    gaps = final_state.get("gaps") or []
    assert any("Fulbright" in (gap.get("claim") or "") for gap in gaps), gaps
    assert final_state.get("generated_document") is None

    rows = document_repo.list_generated_documents_for_application(session, user.id, application.id)
    assert rows == []


def test_scholarship_specific_questions_are_derived_from_requirements_never_a_standard_set(sop_gen_fixture) -> None:
    session, user, profile, scholarship, application = sop_gen_fixture

    requirement = Requirement(
        scholarship_id=scholarship.id,
        category=RequirementCategory.OTHER,
        key="sop_questions",
        value=["Why do you want to study this program?", "How will this scholarship help your career?"],
        mandatory=True,
        value_status="known",
        confidence="verified",
    )
    session.add(requirement)
    session.commit()
    session.expire(scholarship, ["requirements"])

    assert _extract_sop_questions(scholarship) == [
        "Why do you want to study this program?",
        "How will this scholarship help your career?",
    ]

    grounded_draft = SopDraft(
        claims=[
            SopDraftClaim(question="Why do you want to study this program?", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0."),
            SopDraftClaim(question="How will this scholarship help your career?", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0."),
        ]
    )

    with (
        patch("app.workflows.sop_gen.graph._draft_sop", return_value=grounded_draft),
        patch("app.workflows.sop_gen.graph._polish_sop", return_value=grounded_draft.claims),
    ):
        final_state = run_sop_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is False
    questions_answered = {claim.question for claim in final_state["draft"].claims}
    assert questions_answered == {
        "Why do you want to study this program?",
        "How will this scholarship help your career?",
    }


def test_no_specified_questions_falls_back_to_generic_personal_statement_never_assumed_standard_set(sop_gen_fixture) -> None:
    session, user, profile, scholarship, application = sop_gen_fixture

    assert _extract_sop_questions(scholarship) == []

    grounded_draft = SopDraft(
        claims=[SopDraftClaim(question="Personal Statement", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0.")]
    )

    with (
        patch("app.workflows.sop_gen.graph._draft_sop", return_value=grounded_draft) as mock_draft,
        patch("app.workflows.sop_gen.graph._polish_sop", return_value=grounded_draft.claims),
    ):
        final_state = run_sop_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert mock_draft.call_args.args[1] == []  # questions passed to the draft call were empty
    assert final_state["blocked"] is False
    assert final_state["draft"].claims[0].question == "Personal Statement"


def test_successful_generation_populates_source_trace_for_every_claim(sop_gen_fixture) -> None:
    session, user, profile, scholarship, application = sop_gen_fixture

    grounded_draft = SopDraft(
        claims=[
            SopDraftClaim(question="Personal Statement", text="I am Ada Lovelace."),
            SopDraftClaim(question="Personal Statement", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0."),
        ]
    )

    with (
        patch("app.workflows.sop_gen.graph._draft_sop", return_value=grounded_draft),
        patch("app.workflows.sop_gen.graph._polish_sop", return_value=grounded_draft.claims),
    ):
        final_state = run_sop_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is False
    document = final_state["generated_document"]
    assert document is not None

    session.refresh(document)
    assert document.type.value == "sop"
    assert document.source_trace is not None
    assert len(document.source_trace) == len(grounded_draft.claims)
    for entry in document.source_trace:
        assert entry["claim"]
        assert entry["grounded_in"] and entry["grounded_in"] != "unknown"
        assert entry["grounded_in"].startswith("profile.")


def test_polish_that_introduces_ungrounded_content_blocks_generation_not_just_the_first_draft(sop_gen_fixture) -> None:
    """CRITICAL requirement: polish runs AFTER ground_check and must be
    re-verified against the same allowed-facts pool — a polish output that
    introduces something ungrounded blocks the whole run exactly like a
    ground_check failure would, never silently rendering the tampered text
    and never silently falling back to the pre-polish text."""
    session, user, profile, scholarship, application = sop_gen_fixture

    grounded_draft = SopDraft(
        claims=[SopDraftClaim(question="Personal Statement", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0.")]
    )
    polished_but_fabricated = [
        SopDraftClaim(
            question="Personal Statement",
            text="I have a BS in Computer Science from MIT with GPA 3.5/4.0, awarded with highest honors.",
        )
    ]

    with (
        patch("app.workflows.sop_gen.graph._draft_sop", return_value=grounded_draft),
        patch("app.workflows.sop_gen.graph._polish_sop", return_value=polished_but_fabricated),
    ):
        final_state = run_sop_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is True
    gaps = final_state.get("gaps") or []
    assert any("honors" in (gap.get("claim") or "") for gap in gaps), gaps
    assert final_state.get("generated_document") is None

    rows = document_repo.list_generated_documents_for_application(session, user.id, application.id)
    assert rows == []


def test_polish_parse_failure_falls_back_to_pre_polish_text_safely(sop_gen_fixture) -> None:
    """A polish step that fails to produce usable output must never block
    generation over a formatting glitch in a step whose only job is
    rephrasing — the already-gated pre-polish text is safe to render."""
    session, user, profile, scholarship, application = sop_gen_fixture

    grounded_draft = SopDraft(
        claims=[SopDraftClaim(question="Personal Statement", text="I have a BS in Computer Science from MIT with GPA 3.5/4.0.")]
    )

    with (
        patch("app.workflows.sop_gen.graph._draft_sop", return_value=grounded_draft),
        patch("app.workflows.sop_gen.graph._polish_sop", return_value=None),
    ):
        final_state = run_sop_gen(
            session, application=application, profile=profile, documents=[], scholarship=scholarship
        )

    assert final_state["blocked"] is False
    document = final_state["generated_document"]
    assert document is not None
    session.refresh(document)
    assert document.source_trace
    assert document.source_trace[0]["claim"] == grounded_draft.claims[0].text
