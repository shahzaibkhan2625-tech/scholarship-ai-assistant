"""Contract tests (T113): `GET /applications` (tracker) and
`POST /applications/{id}/plan`.

Both endpoints are T126, a later slice — this router file does not define
them yet (`app/api/application.py` only ships `POST /applications`, T107).
Every test below therefore begins with a route-existence precondition via
`find_registered_routes`, mirroring `test_generation_api.py`'s precedent: a
missing route also returns 404 in FastAPI, so a bare status-code assertion
cannot tell "not implemented yet" apart from a correct authorization/
ownership failure (the false-positive class from the Phase 2 2D-i route-scan
bug). Each test MUST fail on that precondition now — for the stated reason,
not a coincidental 404 — and only start exercising the response contract
once T126 lands.

Test isolation is transactional rollback (conftest's `db_session_factory`,
commit d0827b2) — no hand-ordered FK cleanup here.
"""

import re
import uuid

import pytest

from app.models.application import Application
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from app.schemas.plan import ReadinessLabel
from tests.conftest import find_registered_routes


@pytest.fixture
def scholarship_with_requirement(db_session_factory):
    """A scholarship carrying one verified requirement (US6 Scenario 1: "a
    scholarship with verified requirements"), so a future plan-generation
    implementation has something concrete to turn into a checklist item."""
    session = db_session_factory()
    scholarship = Scholarship(
        name="Application Plan Contract Test Scholarship",
        official_scholarship_url="https://example.test/application-plan",
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)

    session.add(
        Requirement(
            scholarship_id=scholarship.id,
            category=RequirementCategory.ACADEMIC,
            key="transcript",
            mandatory=True,
            value_status="known",
            confidence="verified",
        )
    )
    session.commit()

    scholarship_id = scholarship.id
    session.close()
    return scholarship_id


@pytest.fixture
def application_for(db_session_factory, scholarship_with_requirement):
    """Factory: seed an `applications` row directly for a given user,
    bypassing `POST /applications` (already real, T107) so tests can target
    the tracker/plan endpoints in isolation."""

    def _create(user_id: uuid.UUID) -> uuid.UUID:
        session = db_session_factory()
        row = Application(user_id=user_id, scholarship_id=scholarship_with_requirement)
        session.add(row)
        session.commit()
        session.refresh(row)
        application_id = row.id
        session.close()
        return application_id

    return _create


@pytest.fixture
def other_authed_user(client, db_session_factory):
    """A second throwaway user, independent of `authed_user`, for
    cross-user ownership assertions."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    password = "s3cret-pass"  # noqa: S105 - throwaway test fixture credential

    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    user_id = uuid.UUID(signup.json()["id"])

    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    token = login.json()["access_token"]

    return {
        "client": client,
        "token": token,
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {token}"},
    }


def test_get_applications_requires_auth(client) -> None:
    from app.main import app

    assert find_registered_routes(app, "GET", "/applications"), "GET /applications is not registered yet (T126)"

    response = client.get("/applications")
    assert response.status_code == 401


def test_get_applications_returns_only_current_users_applications(
    authed_user, other_authed_user, application_for
) -> None:
    """FR-TRACK-1: the tracker lists the caller's own applications, each
    carrying a readiness assessment (`checklist`) — and never another user's
    rows, mirroring the ownership isolation `test_documents_api.py` already
    asserts for the sibling endpoints."""
    from app.main import app

    assert find_registered_routes(app, "GET", "/applications"), "GET /applications is not registered yet (T126)"

    mine = application_for(authed_user["user_id"])
    theirs = application_for(other_authed_user["user_id"])

    response = authed_user["client"].get("/applications", headers=authed_user["headers"])

    assert response.status_code == 200
    body = response.json()
    ids = {row["id"] for row in body}
    assert str(mine) in ids
    assert str(theirs) not in ids
    for row in body:
        assert "checklist" in row


def test_post_application_plan_requires_auth(client) -> None:
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/plan")), (
        "POST /applications/{id}/plan is not registered yet (T126)"
    )

    response = client.post(f"/applications/{uuid.uuid4()}/plan")
    assert response.status_code == 401


def test_post_application_plan_returns_checklist_with_exactly_one_valid_label_each(
    authed_user, application_for
) -> None:
    """US6 Scenario 1 / FR-PLAN-2 / SC-005: every checklist item carries
    exactly one of the six defined readiness labels."""
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/plan")), (
        "POST /applications/{id}/plan is not registered yet (T126)"
    )

    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/plan", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["application_id"] == str(application_id)
    assert isinstance(body["checklist"], list)
    assert body["checklist"], "a scholarship with a verified requirement must produce at least one checklist item"
    valid_labels = {label.value for label in ReadinessLabel}
    for item in body["checklist"]:
        assert item["readiness_label"] in valid_labels
        assert isinstance(item["readiness_label"], str), "readiness_label must be a single label, never a list"


def test_post_application_plan_for_other_users_application_returns_404(
    authed_user, other_authed_user, application_for
) -> None:
    """Same false-positive concern as `test_documents_api.py`'s equivalent:
    an undefined route also returns 404, so this only becomes a meaningful
    ownership-isolation check once the route-existence precondition passes."""
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/plan")), (
        "POST /applications/{id}/plan is not registered yet (T126)"
    )

    application_id = application_for(authed_user["user_id"])

    intruder_client = other_authed_user["client"]
    intruder_headers = other_authed_user["headers"]
    response = intruder_client.post(f"/applications/{application_id}/plan", headers=intruder_headers)

    assert response.status_code == 404
