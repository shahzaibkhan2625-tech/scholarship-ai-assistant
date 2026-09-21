"""Contract tests (T114): `POST /applications/{id}/assistant/next-step` and
`POST /applications/{id}/submission-approvals` (contracts/openapi.yaml).

Both endpoints belong to T127/T128, a later slice out of this task's scope
lock (T114, T116, T123 only) — `app/api/application.py` does not define them
yet. Every test below therefore MUST fail on the route-existence
precondition via `find_registered_routes`, mirroring `test_application_api.py`
(T113)'s established pattern: a missing route also returns 404 in FastAPI, so
a bare status-code assertion cannot tell "not implemented yet" apart from a
correct authorization/ownership failure. Each test fails on that precondition
now — for the stated reason, not a coincidental 404 — and only starts
exercising the response contract once T127/T128 land.

Test isolation is transactional rollback (conftest's `db_session_factory`) —
no hand-ordered FK cleanup here.
"""

import re
import uuid

import pytest

from app.models.application import Application
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import Scholarship
from tests.conftest import find_registered_routes

NEXT_STEP_PATH = re.compile(r"/applications/\{[^/]+\}/assistant/next-step")
SUBMISSION_APPROVALS_PATH = re.compile(r"/applications/\{[^/]+\}/submission-approvals")


@pytest.fixture
def scholarship_with_requirement(db_session_factory):
    session = db_session_factory()
    scholarship = Scholarship(
        name="Assistant Contract Test Scholarship",
        official_scholarship_url="https://example.test/assistant",
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
    bypassing `POST /applications` so tests can target the assistant/approval
    endpoints in isolation."""

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


def test_post_assistant_next_step_requires_auth(client) -> None:
    from app.main import app

    assert find_registered_routes(app, "POST", NEXT_STEP_PATH), (
        "POST /applications/{id}/assistant/next-step is not registered yet (T127)"
    )

    response = client.post(f"/applications/{uuid.uuid4()}/assistant/next-step")
    assert response.status_code == 401


def test_post_assistant_next_step_returns_assistant_step_result_shape(
    authed_user, application_for
) -> None:
    """FR-APP-2: the agentic-mode result must carry the same shape as
    `AssistantStepResult` (contracts/openapi.yaml) — `step`, `result`,
    `requires_user_input`, `requires_approval`."""
    from app.main import app

    assert find_registered_routes(app, "POST", NEXT_STEP_PATH), (
        "POST /applications/{id}/assistant/next-step is not registered yet (T127)"
    )

    client_, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client_.post(f"/applications/{application_id}/assistant/next-step", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {"step", "result", "requires_user_input", "requires_approval"}


def test_post_assistant_next_step_for_other_users_application_returns_404(
    authed_user, other_authed_user, application_for
) -> None:
    """Same false-positive concern as `test_application_api.py`'s equivalent:
    an undefined route also returns 404, so this only becomes a meaningful
    ownership-isolation check once the route-existence precondition passes."""
    from app.main import app

    assert find_registered_routes(app, "POST", NEXT_STEP_PATH), (
        "POST /applications/{id}/assistant/next-step is not registered yet (T127)"
    )

    application_id = application_for(authed_user["user_id"])

    intruder_client = other_authed_user["client"]
    intruder_headers = other_authed_user["headers"]
    response = intruder_client.post(
        f"/applications/{application_id}/assistant/next-step", headers=intruder_headers
    )

    assert response.status_code == 404


def test_post_submission_approvals_requires_auth(client) -> None:
    from app.main import app

    assert find_registered_routes(app, "POST", SUBMISSION_APPROVALS_PATH), (
        "POST /applications/{id}/submission-approvals is not registered yet (T128)"
    )

    response = client.post(
        f"/applications/{uuid.uuid4()}/submission-approvals", json={"submission_scope": "final-submission"}
    )
    assert response.status_code == 401


def test_post_submission_approvals_creates_approval_scoped_to_the_submission(
    authed_user, application_for
) -> None:
    """FR-APP-3 / constitution Principle III: recording an approval requires
    `submission_scope` (contracts/openapi.yaml requestBody) and the response
    echoes it back — an approval scoped to one submission event only."""
    from app.main import app

    assert find_registered_routes(app, "POST", SUBMISSION_APPROVALS_PATH), (
        "POST /applications/{id}/submission-approvals is not registered yet (T128)"
    )

    client_, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client_.post(
        f"/applications/{application_id}/submission-approvals",
        headers=headers,
        json={"submission_scope": "final-submission"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["application_id"] == str(application_id)
    assert body["submission_scope"] == "final-submission"
    assert body["approved_by"] == str(authed_user["user_id"])


def test_post_submission_approvals_for_other_users_application_returns_404(
    authed_user, other_authed_user, application_for
) -> None:
    from app.main import app

    assert find_registered_routes(app, "POST", SUBMISSION_APPROVALS_PATH), (
        "POST /applications/{id}/submission-approvals is not registered yet (T128)"
    )

    application_id = application_for(authed_user["user_id"])

    intruder_client = other_authed_user["client"]
    intruder_headers = other_authed_user["headers"]
    response = intruder_client.post(
        f"/applications/{application_id}/submission-approvals",
        headers=intruder_headers,
        json={"submission_scope": "final-submission"},
    )

    assert response.status_code == 404
