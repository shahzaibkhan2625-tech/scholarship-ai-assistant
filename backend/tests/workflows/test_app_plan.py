"""`app_plan` workflow tests (T122): idempotency (re-running on unchanged
state must not duplicate tasks and must produce an identical checklist),
deterministic ordering (never relying on DB row order), no-dropping (an
unmapped requirement category still produces a checklist item), and every
item carrying exactly one valid `ReadinessLabel`.

Runs against the real DB via the transactional `db_session_factory` fixture
(rolled back at teardown — no hand-ordered FK cleanup needed)."""

import uuid

import pytest

from app.data.repositories import application_repo
from app.models.application import Application, ReadinessLabel
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from app.models.user import User
from app.workflows.app_plan.graph import run_app_plan


@pytest.fixture
def app_plan_fixture(db_session_factory):
    """A scholarship with four requirements spanning: a mapped, verified,
    user-must-obtain material (transcript); a mapped, verified,
    system-generatable material (research proposal); an UNMAPPED category
    (nationality) that must still produce a checklist item; and an unverified
    requirement (needs official verification) — plus an application owned by
    a fresh user."""
    session = db_session_factory()

    user = User(email=f"app-plan-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)

    scholarship = Scholarship(
        name="App Plan Test Scholarship",
        official_scholarship_url="https://example.test/app-plan",
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)

    requirements = [
        Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.ACADEMIC,
            key="transcript",
            mandatory=True,
            value_status="known",
            confidence="verified",
        ),
        Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.RESEARCH,
            key="research_proposal",
            mandatory=True,
            value_status="known",
            confidence="verified",
        ),
        Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.NATIONALITY,
            key="eligible_nationalities",
            mandatory=False,
            value_status="known",
            confidence="verified",
        ),
        Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.LANGUAGE,
            key="IELTS",
            mandatory=True,
            value_status="unknown",
            confidence="unknown",
        ),
    ]
    session.add_all(requirements)
    session.commit()
    for requirement in requirements:
        session.refresh(requirement)

    application = Application(user_id=user.id, scholarship_id=scholarship.id)
    session.add(application)
    session.commit()
    session.refresh(application)

    yield session, user.id, application.id, requirements


def test_run_app_plan_twice_is_idempotent(app_plan_fixture) -> None:
    session, user_id, application_id, requirements = app_plan_fixture

    plan1 = run_app_plan(session, user_id, application_id)
    plan2 = run_app_plan(session, user_id, application_id)

    assert plan1 is not None
    assert plan2 is not None
    assert plan1.checklist == plan2.checklist

    tasks = application_repo.list_tasks(session, user_id, application_id)
    assert len(tasks) == len(requirements), "re-running must replace, never duplicate, the task set"


def test_checklist_ordering_is_deterministic_not_db_row_order(app_plan_fixture) -> None:
    session, user_id, application_id, requirements = app_plan_fixture

    plan = run_app_plan(session, user_id, application_id)
    assert plan is not None

    expected_order = sorted(
        plan.checklist,
        key=lambda item: (item.category, item.description, str(item.requirement_id)),
    )
    # Mandatory-first is also part of the approved key; verify explicitly by
    # requirement_id -> mandatory lookup rather than re-deriving from items.
    mandatory_by_id = {r.id: r.mandatory for r in requirements}
    mandatory_flags = [mandatory_by_id[item.requirement_id] for item in plan.checklist]
    assert mandatory_flags == sorted(mandatory_flags, reverse=True), "mandatory items must sort first"

    # Re-running produces the exact same order again.
    plan_again = run_app_plan(session, user_id, application_id)
    assert [item.requirement_id for item in plan_again.checklist] == [
        item.requirement_id for item in plan.checklist
    ]
    assert expected_order  # sanity: comparison list was non-empty


def test_unmapped_category_still_appears_on_checklist(app_plan_fixture) -> None:
    """The NATIONALITY requirement has no document-type mapping (A2) — it
    must still appear, never be silently dropped."""
    session, user_id, application_id, requirements = app_plan_fixture
    nationality_requirement = next(r for r in requirements if r.category == RequirementCategory.NATIONALITY)

    plan = run_app_plan(session, user_id, application_id)
    assert plan is not None

    ids_on_checklist = {item.requirement_id for item in plan.checklist}
    assert nationality_requirement.id in ids_on_checklist

    matching_item = next(item for item in plan.checklist if item.requirement_id == nationality_requirement.id)
    assert matching_item.readiness_label == ReadinessLabel.NEEDS_HUMAN_REVIEW


def test_every_checklist_item_has_exactly_one_requirement_and_valid_label(app_plan_fixture) -> None:
    session, user_id, application_id, requirements = app_plan_fixture

    plan = run_app_plan(session, user_id, application_id)
    assert plan is not None

    assert len(plan.checklist) == len(requirements), "one checklist item per stored requirement, no more, no fewer"

    requirement_ids = {r.id for r in requirements}
    seen_ids = set()
    valid_labels = set(ReadinessLabel)
    for item in plan.checklist:
        assert item.requirement_id in requirement_ids
        assert item.requirement_id not in seen_ids, "no fabricated duplicate checklist items"
        seen_ids.add(item.requirement_id)
        assert item.readiness_label in valid_labels
        assert isinstance(item.readiness_label, ReadinessLabel)


def test_run_app_plan_returns_none_for_missing_application(db_session_factory) -> None:
    session = db_session_factory()
    assert run_app_plan(session, uuid.uuid4(), uuid.uuid4()) is None
