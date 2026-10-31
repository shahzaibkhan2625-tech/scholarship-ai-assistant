"""010 US5 T392: size limit, content checks and clear errors at both upload routes (FR-UPLOAD-1, -2).

Starts at `POST /applications/{id}/documents` and `POST /profile/import-cv`. Storage is replaced
by a recorder so "nothing is stored" is a count, not an assumption.
"""

import io
import re
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from app.models.document import ApplicationDocument
from app.schemas.profile import ProfileUpdateRequest
from app.tools.extract_document_fields import ExtractedDocumentFields
from tests.api.test_documents_api import _text_pdf_bytes, application_for, scholarship  # noqa: F401 (fixtures)

LIMIT = 10_485_760
TOO_LARGE = "File too large (maximum 10 MB)"
ONLY_PDF = "Only PDF files are accepted"
CV_UNSUPPORTED = "Unsupported file: send a PDF or a UTF-8 plain-text file"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _RecordingStorage:
    def __init__(self):
        self.saves = 0

    def save(self, user_id, filename, data):
        self.saves += 1
        return f"documents/{user_id}/{self.saves}.pdf"


def _padded_pdf(size: int) -> bytes:
    base = _text_pdf_bytes()
    return base + b"\n%" + b"0" * (size - len(base) - 2)


def _encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user-pw", owner_pw="owner-pw")
    doc.close()
    return data


BAD_PDFS = {
    "corrupt": b"%PDF-1.4 this is not a real pdf body",
    "truncated": _text_pdf_bytes()[:200],
    "encrypted": _encrypted_pdf(),
}


@pytest.fixture
def storage():
    recorder = _RecordingStorage()
    with patch("app.api.documents.get_storage", return_value=recorder):
        yield recorder


@pytest.fixture
def extractors():
    with patch("app.workflows.doc_pipeline.graph.extract_document_fields", return_value=ExtractedDocumentFields()), patch(
        "app.api.profile.extract_profile_from_text", return_value=ProfileUpdateRequest(nationality="Pakistani")
    ) as cv:
        yield cv


@pytest.fixture
def post_document(authed_user, application_for, storage, extractors):  # noqa: F811
    application_id = application_for(authed_user["user_id"])

    def _post(data: bytes, name: str = "transcript.pdf"):
        return authed_user["client"].post(
            f"/applications/{application_id}/documents",
            headers=authed_user["headers"],
            files={"file": (name, io.BytesIO(data), "application/octet-stream")},
            data={"type": "transcript"},
        )

    _post.application_id = application_id
    return _post


@pytest.fixture
def post_cv(authed_user, extractors):
    def _post(data: bytes, name: str = "cv.txt"):
        return authed_user["client"].post(
            "/profile/import-cv",
            headers=authed_user["headers"],
            files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        )

    return _post


def _rows(db_session_factory, application_id) -> int:
    with db_session_factory() as session:
        return session.query(ApplicationDocument).filter(ApplicationDocument.application_id == application_id).count()


# -- documents ---------------------------------------------------------------------------


def test_document_of_exactly_10_mib_is_accepted(post_document, storage):
    assert post_document(_padded_pdf(LIMIT)).status_code == 201
    assert storage.saves == 1


def test_document_one_byte_over_10_mib_is_413_and_stores_nothing(post_document, storage, db_session_factory):
    response = post_document(_padded_pdf(LIMIT + 1))
    assert (response.status_code, response.json()["detail"]) == (413, TOO_LARGE)
    assert storage.saves == 0 and _rows(db_session_factory, post_document.application_id) == 0


def test_eleven_megabyte_document_is_refused_by_the_body_limit(post_document, storage, db_session_factory):
    response = post_document(_padded_pdf(11_000_000))
    assert (response.status_code, response.json()["detail"]) == (413, TOO_LARGE)
    assert storage.saves == 0 and _rows(db_session_factory, post_document.application_id) == 0


def test_text_file_named_pdf_is_415(post_document, storage, db_session_factory):
    response = post_document(b"plain text pretending", "x.pdf")
    assert (response.status_code, response.json()["detail"]) == (415, ONLY_PDF)
    assert storage.saves == 0 and _rows(db_session_factory, post_document.application_id) == 0


def test_real_pdf_with_any_name_is_accepted_as_a_document(post_document):
    assert post_document(_text_pdf_bytes(), "x.bin").status_code == 201


def test_empty_document_is_400(post_document, storage, db_session_factory):
    response = post_document(b"")
    assert (response.status_code, response.json()["detail"]) == (400, "File is empty")
    assert storage.saves == 0 and _rows(db_session_factory, post_document.application_id) == 0


@pytest.mark.parametrize("kind", list(BAD_PDFS))
def test_unreadable_pdf_is_422_at_the_document_route(post_document, kind):
    assert post_document(BAD_PDFS[kind]).status_code == 422


# -- CV import ----------------------------------------------------------------------------


def test_cv_pdf_of_exactly_10_mib_is_accepted(post_cv):
    assert post_cv(_padded_pdf(LIMIT), "cv.pdf").status_code == 200


def test_cv_one_byte_over_10_mib_is_413(post_cv, extractors):
    response = post_cv(_padded_pdf(LIMIT + 1), "cv.pdf")
    assert (response.status_code, response.json()["detail"]) == (413, TOO_LARGE)
    extractors.assert_not_called()


def test_cv_real_pdf_with_any_name_is_accepted(post_cv):
    assert post_cv(_text_pdf_bytes(), "x.bin").status_code == 200


def test_cv_utf8_text_is_accepted_and_nothing_is_saved(post_cv, authed_user):
    response = post_cv("José, Pakistani, applying for MS".encode("utf-8"), "cv.txt")
    assert response.status_code == 200 and response.json()["nationality"] == "Pakistani"
    profile = authed_user["client"].get("/profile", headers=authed_user["headers"])
    assert profile.json()["nationality"] is None


def test_cv_text_named_pdf_is_decided_by_content(post_cv):
    assert post_cv(b"plain utf-8 text", "x.pdf").status_code == 200


@pytest.mark.parametrize(
    "data",
    [b"caf\xe9", PNG, b"text with a \x00 NUL byte"],
    ids=["latin1", "png", "nul"],
)
def test_cv_invalid_utf8_binary_and_nul_are_415(post_cv, extractors, data):
    response = post_cv(data, "cv.txt")
    assert (response.status_code, response.json()["detail"]) == (415, CV_UNSUPPORTED)
    extractors.assert_not_called()


def test_empty_cv_is_400(post_cv):
    response = post_cv(b"")
    assert (response.status_code, response.json()["detail"]) == (400, "File is empty")


@pytest.mark.parametrize("kind", list(BAD_PDFS))
def test_unreadable_pdf_is_422_at_the_cv_route(post_cv, extractors, kind):
    response = post_cv(BAD_PDFS[kind], "cv.pdf")
    assert (response.status_code, response.json()["detail"]) == (422, "We could not read this PDF")
    extractors.assert_not_called()


def test_cv_import_never_decodes_with_errors_ignore():
    source = Path(__file__).resolve().parents[2] / "app"
    for name in ("api/profile.py", "core/uploads.py"):
        assert not re.search(r"errors\s*=\s*['\"]ignore['\"]", (source / name).read_text(encoding="utf-8")), name
