"""Contract tests (T093): `POST /applications`, `POST /applications/{id}/documents`.

Slice 3A ships the data layer only (models, migration, repositories) — the
`application`/`documents` routers do not exist yet and are not wired into
`app.main`. Every test below is expected to FAIL until those routers land
in a later slice; this file defines the contract they must satisfy then.
"""

import re
import uuid

import pytest

from app.models.application import Application
from app.models.scholarship import Scholarship
from app.models.user import User
from tests.conftest import find_registered_routes


@pytest.fixture
def scholarship(db_session_factory):
    session = db_session_factory()
    row = Scholarship(name="Contract Test Scholarship", official_scholarship_url="https://example.test/scholarship")
    session.add(row)
    session.commit()
    session.refresh(row)
    scholarship_id = row.id
    session.close()

    yield scholarship_id

    session = db_session_factory()
    existing = session.get(Scholarship, scholarship_id)
    if existing is not None:
        session.delete(existing)
        session.commit()
    session.close()


@pytest.fixture
def other_authed_user(client, db_session_factory):
    """A second throwaway user, independent of `authed_user`, so
    cross-user-isolation assertions have two distinct identities to work
    with."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    password = "s3cret-pass"  # noqa: S105 - throwaway test fixture credential

    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    user_id = uuid.UUID(signup.json()["id"])

    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    token = login.json()["access_token"]

    yield {
        "client": client,
        "token": token,
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {token}"},
    }

    session = db_session_factory()
    user = session.get(User, user_id)
    if user is not None:
        session.delete(user)
        session.commit()
    session.close()


@pytest.fixture
def application_for(db_session_factory, scholarship):
    """Factory: seed an `applications` row directly for a given user,
    bypassing the not-yet-existing `POST /applications` route, so the
    document-upload tests can exercise `/applications/{id}/documents` in
    isolation (T098's model + T100's migration are real; the route is not)."""

    created_ids: list[uuid.UUID] = []

    def _create(user_id: uuid.UUID) -> uuid.UUID:
        session = db_session_factory()
        row = Application(user_id=user_id, scholarship_id=scholarship)
        session.add(row)
        session.commit()
        session.refresh(row)
        application_id = row.id
        session.close()
        created_ids.append(application_id)
        return application_id

    yield _create

    session = db_session_factory()
    for application_id in created_ids:
        row = session.get(Application, application_id)
        if row is not None:
            session.delete(row)
    session.commit()
    session.close()


def test_create_application_requires_auth(client, scholarship) -> None:
    response = client.post("/applications", json={"scholarship_id": str(scholarship)})
    assert response.status_code == 401


def test_create_application_returns_201_with_expected_shape(authed_user, scholarship) -> None:
    client, headers = authed_user["client"], authed_user["headers"]

    response = client.post("/applications", headers=headers, json={"scholarship_id": str(scholarship)})

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["scholarship_id"] == str(scholarship)
    assert "status" in body


def test_upload_document_requires_auth(client) -> None:
    response = client.post(
        f"/applications/{uuid.uuid4()}/documents",
        files={"file": ("transcript.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"type": "transcript"},
    )
    assert response.status_code == 401


def test_upload_document_for_other_users_application_returns_404(
    authed_user, other_authed_user, application_for
) -> None:
    """An undefined route also returns 404 in FastAPI, so the 404 assertion
    below is meaningless as an authorization check until the route actually
    exists (same false-positive class as the Phase 2 "no POST /sources"
    governance-test bug). This precondition forces the test to fail now and
    only pass on route-existence + real ownership-check grounds later."""
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/documents")), (
        "POST /applications/{id}/documents is not registered yet"
    )

    application_id = application_for(authed_user["user_id"])

    intruder_client = other_authed_user["client"]
    intruder_headers = other_authed_user["headers"]
    response = intruder_client.post(
        f"/applications/{application_id}/documents",
        headers=intruder_headers,
        files={"file": ("transcript.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"type": "transcript"},
    )

    assert response.status_code == 404


def test_upload_document_returns_201_with_expected_shape(authed_user, application_for) -> None:
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(
        f"/applications/{application_id}/documents",
        headers=headers,
        files={"file": ("transcript.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"type": "transcript"},
    )

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["type"] == "unclassified"
    assert body["parsed_meta"] is None
    assert body["satisfies_requirement_id"] is None
