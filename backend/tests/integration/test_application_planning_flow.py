"""Integration test covering spec.md US6 Acceptance Scenarios 1-5 (T115):
application planning and assisted, human-approved submission, exercised
through the real HTTP contract (`POST .../plan`, `POST .../assistant/
next-step`, `POST .../submission-approvals`) rather than by asserting on
internal function calls.

No LLM call exists anywhere in the exercised call chain: `run_app_plan`
(app_plan/graph.py) and `run_submit_prep` (submit_prep/graph.py) are both
explicitly documented as LLM-free, and `run_application_assistant`
(agents/application/agent.py) only ever calls those two -- confirmed by
reading all three modules, not merely assumed. Nothing is mocked here as a
result.

Test isolation is transactional rollback (conftest's `db_session_factory`) --
no hand-ordered FK cleanup here.
"""

import uuid

from app.models.application import Application, ReadinessLabel
from app.models.document import ApplicationDocument, DocumentType
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from app.workflows.submit_prep.graph import run_submit_prep

import pytest


@pytest.fixture
def full_state_scholarship(db_session_factory):
    """A scholarship with five requirements, each landing on a different
    readiness label: `transcript` is COMPLETE (satisfied by an uploaded
    document, wired up per-user by `application_with_full_state` below);
    `portfolio` is a USER_HELD material with no upload -> MISSING; `sop` is a
    SYSTEM_GENERATABLE material with no draft -> AI_CAN_GENERATE;
    `ielts` is unverified -> NEEDS_OFFICIAL_VERIFICATION; and
    `eligible_nationalities` has no document-type mapping -> the
    NEEDS_HUMAN_REVIEW catch-all. This one fixture backs both Acceptance
    Scenario 1 (several requirements in different states) and Scenario 2
    (the MISSING vs AI_CAN_GENERATE distinction, portfolio vs sop)."""
    session = db_session_factory()
    scholarship = Scholarship(
        name="Planning Flow Test Scholarship",
        official_scholarship_url="https://example.test/planning-flow",
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)

    requirements = {
        "transcript": Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.ACADEMIC,
            key="transcript",
            mandatory=True,
            value_status="known",
            confidence="verified",
        ),
        "portfolio": Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.OTHER,
            key="portfolio",
            mandatory=True,
            value_status="known",
            confidence="verified",
        ),
        "sop": Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.OTHER,
            key="sop",
            mandatory=True,
            value_status="known",
            confidence="verified",
        ),
        "ielts": Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.LANGUAGE,
            key="ielts",
            mandatory=True,
            value_status="unknown",
            confidence="unknown",
        ),
        "eligible_nationalities": Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.NATIONALITY,
            key="eligible_nationalities",
            mandatory=False,
            value_status="known",
            confidence="verified",
        ),
    }
    session.add_all(requirements.values())
    session.commit()
    for requirement in requirements.values():
        session.refresh(requirement)

    requirement_ids = {name: requirement.id for name, requirement in requirements.items()}
    scholarship_id = scholarship.id
    session.close()
    return scholarship_id, requirement_ids


@pytest.fixture
def application_with_full_state(db_session_factory, full_state_scholarship):
    """Factory: seeds an application against `full_state_scholarship` for a
    given user, plus one uploaded document satisfying the `transcript`
    requirement (so that requirement lands on COMPLETE, not USER_MUST_OBTAIN)."""
    scholarship_id, requirement_ids = full_state_scholarship

    def _create(user_id: uuid.UUID) -> uuid.UUID:
        session = db_session_factory()
        application = Application(user_id=user_id, scholarship_id=scholarship_id)
        session.add(application)
        session.commit()
        session.refresh(application)

        session.add(
            ApplicationDocument(
                user_id=user_id,
                application_id=application.id,
                type=DocumentType.TRANSCRIPT,
                file_ref="uploads/transcript.pdf",
                satisfies_requirement_id=requirement_ids["transcript"],
                checksum="transcript-checksum-1",
            )
        )
        session.commit()

        application_id = application.id
        session.close()
        return application_id

    return _create


@pytest.fixture
def empty_scholarship_id(db_session_factory):
    """A scholarship with zero requirements -- a trivially "fully ready"
    application (no possible blocking task) for Scenario 3/4's approval-gate
    tests, without needing to first resolve every requirement to COMPLETE."""
    session = db_session_factory()
    scholarship = Scholarship(
        name="No Requirements Test Scholarship",
        official_scholarship_url="https://example.test/no-requirements",
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)
    scholarship_id = scholarship.id
    session.close()
    return scholarship_id


@pytest.fixture
def application_with_no_requirements(db_session_factory, empty_scholarship_id):
    def _create(user_id: uuid.UUID) -> uuid.UUID:
        session = db_session_factory()
        application = Application(user_id=user_id, scholarship_id=empty_scholarship_id)
        session.add(application)
        session.commit()
        session.refresh(application)
        application_id = application.id
        session.close()
        return application_id

    return _create


# --- Scenario 1: every checklist item carries exactly one readiness label ---


def test_every_checklist_item_has_exactly_one_readiness_label(authed_user, application_with_full_state) -> None:
    """US6 Scenario 1: a scholarship with requirements in several different
    states (satisfied, missing, unverified, ...) -> every checklist item
    returned by `POST .../plan` carries exactly one valid `ReadinessLabel`.
    This is already structurally guaranteed by `ChecklistItem`'s type (a
    single non-optional field, never a list), but this test proves it
    end-to-end through the real HTTP contract, not just the type system."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_with_full_state(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/plan", headers=headers)

    assert response.status_code == 200
    checklist = response.json()["checklist"]
    assert len(checklist) == 5

    valid_labels = {label.value for label in ReadinessLabel}
    for item in checklist:
        assert isinstance(item["readiness_label"], str), "readiness_label must be a single label, never a list"
        assert item["readiness_label"] in valid_labels

    labels_by_key = {item["description"]: item["readiness_label"] for item in checklist}
    assert labels_by_key["transcript"] == ReadinessLabel.COMPLETE.value
    assert labels_by_key["portfolio"] == ReadinessLabel.MISSING.value
    assert labels_by_key["sop"] == ReadinessLabel.AI_CAN_GENERATE.value
    assert labels_by_key["ielts"] == ReadinessLabel.NEEDS_OFFICIAL_VERIFICATION.value
    assert labels_by_key["eligible_nationalities"] == ReadinessLabel.NEEDS_HUMAN_REVIEW.value


# --- Scenario 1 (cont'd): MISSING vs AI_CAN_GENERATE distinction ---


def test_missing_and_ai_can_generate_are_distinguished(authed_user, application_with_full_state) -> None:
    """Regression guard for the transcript mis-classification found during
    4B's review: a USER_HELD material not yet uploaded (`portfolio`) must be
    labeled MISSING, and a SYSTEM_GENERATABLE material with no draft yet
    (`sop`) must be labeled AI_CAN_GENERATE -- never collapsed to the same
    label. Would fail if that regression reappeared."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_with_full_state(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/plan", headers=headers)
    assert response.status_code == 200
    checklist = response.json()["checklist"]

    labels_by_key = {item["description"]: item["readiness_label"] for item in checklist}
    assert labels_by_key["portfolio"] == ReadinessLabel.MISSING.value
    assert labels_by_key["sop"] == ReadinessLabel.AI_CAN_GENERATE.value
    assert labels_by_key["portfolio"] != labels_by_key["sop"]


# --- Scenario 3: no transmission without approval ---


def test_next_step_never_reaches_ready_to_submit_without_a_prior_approval(
    authed_user, application_with_no_requirements
) -> None:
    """US6 Scenario 3 / FR-APP-3: a fully-ready application (no blocking
    tasks) must land on `awaiting_submission_approval` -- NEVER
    `ready_to_submit` -- until an explicit approval is recorded for the
    current content. Only after recording that approval does the same call
    return `ready_to_submit`."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_with_no_requirements(authed_user["user_id"])

    before_approval = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert before_approval.status_code == 200
    before_body = before_approval.json()
    assert before_body["step"] == "awaiting_submission_approval"
    assert before_body["requires_approval"] is True
    assert before_body["step"] != "ready_to_submit"

    fingerprint = before_body["result"]["content_fingerprint"]

    approval_response = client.post(
        f"/applications/{application_id}/submission-approvals",
        headers=headers,
        json={"submission_scope": "final-submission"},
    )
    assert approval_response.status_code == 201
    assert approval_response.json()["content_fingerprint"] == fingerprint

    after_approval = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert after_approval.status_code == 200
    after_body = after_approval.json()
    assert after_body["step"] == "ready_to_submit"
    assert after_body["requires_approval"] is False


# --- Scenario 4: approval does not carry forward ---


def test_approval_does_not_carry_forward_to_changed_content_or_a_different_scope(
    authed_user, application_with_no_requirements, db_session_factory
) -> None:
    """US6 Scenario 4 / FR-APP-3: an approval recorded for one submission
    does not authorize (a) the same scope once the application's content has
    since changed, or (b) a different `submission_scope` for the same,
    unchanged content."""
    client, headers = authed_user["client"], authed_user["headers"]
    user_id = authed_user["user_id"]
    application_id = application_with_no_requirements(user_id)

    first = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert first.json()["step"] == "awaiting_submission_approval"

    approval_response = client.post(
        f"/applications/{application_id}/submission-approvals",
        headers=headers,
        json={"submission_scope": "final-submission"},
    )
    assert approval_response.status_code == 201

    ready = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert ready.json()["step"] == "ready_to_submit"
    approved_fingerprint = ready.json()["result"]["content_fingerprint"]

    # Scope dimension: the same approved content, but a different
    # submission_scope than the one actually approved ("final-submission"),
    # must not be authorized. `next-step`'s scope is a fixed constant
    # (DEFAULT_SUBMISSION_SCOPE), so this is exercised directly through the
    # shared `run_submit_prep` workflow entry point -- the same function
    # `next-step` itself calls -- rather than duplicating
    # test_submission_approval_scope.py's pure-function unit coverage.
    session = db_session_factory()
    scope_check = run_submit_prep(session, user_id, application_id, submission_scope="resubmission-after-review")
    session.close()
    assert scope_check is not None
    assert scope_check["content_fingerprint"] == approved_fingerprint
    assert scope_check["authorized"] is False

    # Content dimension: change the application's content (add a document
    # unrelated to any requirement, which is enough to change the assembled
    # package) and confirm the earlier "final-submission" approval no longer
    # authorizes it.
    session = db_session_factory()
    session.add(
        ApplicationDocument(
            user_id=user_id,
            application_id=application_id,
            type=DocumentType.OTHER,
            file_ref="uploads/extra.pdf",
            checksum="extra-doc-checksum",
        )
    )
    session.commit()
    session.close()

    after_change = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert after_change.status_code == 200
    after_body = after_change.json()
    assert after_body["step"] == "awaiting_submission_approval"
    assert after_body["requires_approval"] is True
    assert after_body["result"]["content_fingerprint"] != approved_fingerprint


# --- Scenario 5: agentic/deterministic parity ---


def test_agentic_and_deterministic_modes_agree_on_blocking_tasks(
    authed_user, application_with_full_state
) -> None:
    """US6 Scenario 5: for the same application in the same state, the
    button (`POST .../plan`) and the agent (`POST .../assistant/next-step`)
    must agree on exactly which items are blocking."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_with_full_state(authed_user["user_id"])

    plan_response = client.post(f"/applications/{application_id}/plan", headers=headers)
    assert plan_response.status_code == 200
    checklist = plan_response.json()["checklist"]

    blocking_labels = {
        ReadinessLabel.MISSING.value,
        ReadinessLabel.USER_MUST_OBTAIN.value,
        ReadinessLabel.AI_CAN_GENERATE.value,
    }
    # `id`, `due_date`, and `requirement_id` are present on the button's full
    # ChecklistItem but absent from the agent's blocking_tasks entries by
    # design (agent.py's action_required branch only ever emits
    # description/category/readiness_label per item) -- excluded from both
    # sides of the comparison so it isn't penalized for a shape neither
    # response actually claims to share.
    button_blocking = {
        (item["description"], item["category"], item["readiness_label"])
        for item in checklist
        if item["readiness_label"] in blocking_labels
    }
    assert button_blocking, "fixture must contain blocking items for this comparison to be meaningful"

    next_step_response = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert next_step_response.status_code == 200
    next_step_body = next_step_response.json()
    assert next_step_body["step"] == "action_required"
    assert next_step_body["requires_user_input"] is True

    agent_blocking = {
        (task["description"], task["category"], task["readiness_label"])
        for task in next_step_body["result"]["blocking_tasks"]
    }

    assert button_blocking == agent_blocking


def test_agentic_and_deterministic_modes_agree_when_fully_ready(
    authed_user, application_with_no_requirements
) -> None:
    """US6 Scenario 5 (fully-ready case): the button reports an empty
    blocking set (an empty checklist) and the agent, consistent with
    Scenario 3's ordering (no approval on file yet), reports
    `awaiting_submission_approval` rather than `action_required`."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_with_no_requirements(authed_user["user_id"])

    plan_response = client.post(f"/applications/{application_id}/plan", headers=headers)
    assert plan_response.status_code == 200
    assert plan_response.json()["checklist"] == []

    next_step_response = client.post(f"/applications/{application_id}/assistant/next-step", headers=headers)
    assert next_step_response.status_code == 200
    assert next_step_response.json()["step"] == "awaiting_submission_approval"
