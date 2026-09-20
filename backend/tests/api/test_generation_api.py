"""Contract tests (T094): `POST /applications/{id}/generate/cv`, `POST
/applications/{id}/generate/sop`.

Slice 3D ships the `cv_gen` workflow only (T109). The generation API router
(T111) and its wiring into `app.main` (T112) are Slice 3E and do not exist
yet — every test below asserts route registration first via
`find_registered_routes`, so it fails for that explicit, stated reason
rather than coincidentally on an undefined-route 404 (same false-positive
class as the Phase 2 2D-i route-scan bug and slice 3A's
`test_documents_api.py` precedent: `POST /applications/{id}/documents` before
T108 landed)."""

import re
import uuid

import pytest

from app.models.application import Application
from app.models.document import ApplicationDocument, GeneratedDocument
from app.models.scholarship import Scholarship
from tests.conftest import find_registered_routes


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
def scholarship(db_session_factory):
    session = db_session_factory()
    row = Scholarship(name="Generation Contract Test Scholarship", official_scholarship_url="https://example.test/generation")
    session.add(row)
    session.commit()
    session.refresh(row)
    scholarship_id = row.id
    session.close()

    yield scholarship_id

    session = db_session_factory()
    orphaned_ids = [
        app_id for (app_id,) in session.query(Application.id).filter(Application.scholarship_id == scholarship_id)
    ]
    _delete_applications_and_children(session, orphaned_ids)
    existing = session.get(Scholarship, scholarship_id)
    if existing is not None:
        session.delete(existing)
        session.commit()
    session.close()


@pytest.fixture
def application_for(db_session_factory, scholarship):
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
    _delete_applications_and_children(session, created_ids)
    session.close()


def test_generate_cv_endpoint_not_registered_yet(authed_user, application_for) -> None:
    """T111/T112 (generation API + router wiring) are Slice 3E — this MUST
    fail now, and for the stated reason (route not registered), not a
    coincidental 404 that would also occur on e.g. a wrong application id."""
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/generate/cv")), (
        "POST /applications/{id}/generate/cv is not registered yet (Slice 3E, T111/T112)"
    )

    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/generate/cv", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["type"] == "cv"
    assert body["source_trace"], "every factual claim must carry a source_trace entry (FR-GEN-2/SC-004)"


def test_generate_sop_endpoint_not_registered_yet(authed_user, application_for) -> None:
    """Same contract, mirrored for SOP generation (T110/T111, also not in
    this slice's scope)."""
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/generate/sop")), (
        "POST /applications/{id}/generate/sop is not registered yet (Slice 3E, T111/T112)"
    )

    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/generate/sop", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["type"] == "sop"
    assert body["source_trace"], "every factual claim must carry a source_trace entry (FR-GEN-2/SC-004)"


def test_generate_cv_reports_409_on_information_gap_not_invented_content(authed_user, application_for) -> None:
    """US5 Scenario 5 / FR-GEN-2: a gap in required information is reported
    (409), never filled with invented content. Also gated on route
    registration for the same reason as the tests above."""
    from app.main import app

    assert find_registered_routes(app, "POST", re.compile(r"/applications/\{[^/]+\}/generate/cv")), (
        "POST /applications/{id}/generate/cv is not registered yet (Slice 3E, T111/T112)"
    )

    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(f"/applications/{application_id}/generate/cv", headers=headers)

    assert response.status_code == 409
