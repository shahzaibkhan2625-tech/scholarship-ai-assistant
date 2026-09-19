"""Contract tests (T093): `POST /applications`, `POST /applications/{id}/documents`.

Slice 3A ships the data layer only (models, migration, repositories) — the
`application`/`documents` routers do not exist yet and are not wired into
`app.main`. Every test below is expected to FAIL until those routers land
in a later slice; this file defines the contract they must satisfy then.
"""

import re
import uuid
from unittest.mock import patch

import pymupdf
import pytest

from app.models.application import Application
from app.models.document import ApplicationDocument, GeneratedDocument
from app.models.scholarship import Scholarship
from app.models.user import User
from app.tools.extract_document_fields import ExtractedDocumentFields
from tests.conftest import find_registered_routes


def _text_pdf_bytes(text: str = "Hello world sample transcript text") -> bytes:
    """Builds a real, valid, parseable PDF in-memory (mirrors
    `tests/tools/test_pdf_parse.py`) rather than a checked-in binary fixture
    or hand-typed fake bytes — `pdf_parse` (T104) genuinely opens this."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _image_only_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 50, 50), False)
    pix.set_rect(pix.irect, (200, 0, 0))
    page.insert_image(pymupdf.Rect(10, 10, 60, 60), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def _delete_applications_and_children(session, application_ids: list[uuid.UUID]) -> None:
    """Child-first cleanup for a batch of `applications` rows: any
    `application_documents`/`generated_documents` referencing them, then the
    `applications` rows themselves. Shared by `scholarship` and
    `application_for` below since both may need to remove rows neither of
    them tracked (e.g. one created directly via `POST /applications`)."""
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
    row = Scholarship(name="Contract Test Scholarship", official_scholarship_url="https://example.test/scholarship")
    session.add(row)
    session.commit()
    session.refresh(row)
    scholarship_id = row.id
    session.close()

    yield scholarship_id

    session = db_session_factory()
    # Any application referencing this scholarship must go first, regardless
    # of how it was created (`application_for` already cleans up its own,
    # but e.g. `POST /applications` in test_create_application_... creates
    # one this fixture never tracked) — otherwise the delete below trips
    # `applications_scholarship_id_fkey`.
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
    _delete_applications_and_children(session, created_ids)
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

    # The target scholarship (see `scholarship` fixture) declares no
    # requirements at all, so there is nothing for doc_pipeline to associate
    # or satisfy-check against regardless of what this file parses to —
    # `type`/`parsed_meta`/`satisfies_requirement_id` on the response reflect
    # that: `type` is never reclassified in this slice, and `parsed_meta`
    # stays null because extraction has nothing informative to report for
    # generic filler text. The LLM extraction call itself is mocked (as
    # every other LLM-touching test in this suite does) so this stays a
    # fast, deterministic contract test.
    with patch(
        "app.workflows.doc_pipeline.graph.extract_document_fields",
        return_value=ExtractedDocumentFields(),
    ):
        response = client.post(
            f"/applications/{application_id}/documents",
            headers=headers,
            files={"file": ("transcript.pdf", _text_pdf_bytes(), "application/pdf")},
            data={"type": "transcript"},
        )

    assert response.status_code == 201
    body = response.json()
    assert uuid.UUID(body["id"])
    assert body["type"] == "unclassified"
    assert body["parsed_meta"] is None
    assert body["satisfies_requirement_id"] is None


def test_upload_corrupt_document_returns_422_and_preserves_the_row(authed_user, application_for, db_session_factory) -> None:
    """Edge Cases: "a document upload fails to parse -> the user is told
    parsing failed and asked to retry/replace, not silently ignored." The
    file + row must already be durably persisted before parsing runs, so the
    upload is retryable and the failure is inspectable even after a 422."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(
        f"/applications/{application_id}/documents",
        headers=headers,
        files={"file": ("corrupt.pdf", b"%PDF-1.4 this is not a real pdf body", "application/pdf")},
        data={"type": "transcript"},
    )

    assert response.status_code == 422

    session = db_session_factory()
    rows = (
        session.query(ApplicationDocument)
        .filter(ApplicationDocument.application_id == application_id)
        .all()
    )
    session.close()

    assert len(rows) == 1
    assert rows[0].parsed_meta is None
    assert rows[0].inconsistency_flags is not None
    assert any(flag.get("type") == "parse_error" for flag in rows[0].inconsistency_flags)


def test_upload_scanned_document_returns_201_with_needs_ocr_flag(authed_user, application_for) -> None:
    """A PDF that opens and parses fine but has no extractable text (a
    scanned/image-only document) is neither a parse failure nor "the
    document contains no information" — it must be reported as needing OCR
    or a text-based copy, with a normal 201."""
    client, headers = authed_user["client"], authed_user["headers"]
    application_id = application_for(authed_user["user_id"])

    response = client.post(
        f"/applications/{application_id}/documents",
        headers=headers,
        files={"file": ("scan.pdf", _image_only_pdf_bytes(), "application/pdf")},
        data={"type": "transcript"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["parsed_meta"] is None
    assert body["inconsistency_flags"] is not None
    assert any(flag.get("type") == "needs_ocr" for flag in body["inconsistency_flags"])
