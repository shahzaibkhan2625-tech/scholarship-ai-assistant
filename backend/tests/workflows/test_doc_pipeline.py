"""`doc_pipeline` workflow tests (T105): a `PdfParseError` surfaces as an
explicit failure and never as an empty document; a scanned/text-empty PDF is
reported as needing OCR (never a failure, never "the document has no
information"); a genuine inconsistency between profile and document data is
flagged, never silently resolved. Runs against the real DB (skip-if-
unreachable, mirrors tests/workflows/test_ingestion.py); every row created
is torn down. The LLM extraction call is mocked.
"""

import uuid
from unittest.mock import patch

import pymupdf
import pytest

from app.data.repositories import document_repo
from app.models.application import Application
from app.models.document import ApplicationDocument
from app.models.profile import EducationRecord, Profile
from app.models.scholarship import Scholarship
from app.models.user import User
from app.tools.extract_document_fields import ExtractedDocumentFields
from app.workflows.doc_pipeline.graph import run_doc_pipeline


def _text_pdf_bytes(text: str = "Sample transcript text") -> bytes:
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


@pytest.fixture
def pipeline_fixture(db_session_factory):
    """Creates a user + profile + scholarship + application + a durably
    persisted `application_documents` row, ready for `run_doc_pipeline` to
    process — mirroring T108's own ordering guarantee (upload persisted
    before parsing ever runs)."""
    session = db_session_factory()

    user = User(email=f"doc-pipeline-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)

    profile = Profile(user_id=user.id)
    session.add(profile)
    session.commit()
    session.refresh(profile)

    scholarship = Scholarship(
        name="Doc Pipeline Test Scholarship", official_scholarship_url="https://example.test/doc-pipeline"
    )
    session.add(scholarship)
    session.commit()
    session.refresh(scholarship)

    application = Application(user_id=user.id, scholarship_id=scholarship.id)
    session.add(application)
    session.commit()
    session.refresh(application)

    def _make_document() -> ApplicationDocument:
        return document_repo.create_application_document(
            session,
            user.id,
            application.id,
            file_ref=f"documents/{user.id}/{uuid.uuid4()}.pdf",
            checksum="deadbeef",
        )

    yield session, profile, scholarship, _make_document

    session.query(ApplicationDocument).filter(ApplicationDocument.application_id == application.id).delete()
    session.query(Application).filter(Application.id == application.id).delete()
    session.query(EducationRecord).filter(EducationRecord.profile_id == profile.id).delete()
    session.query(Profile).filter(Profile.id == profile.id).delete()
    session.query(Scholarship).filter(Scholarship.id == scholarship.id).delete()
    session.query(User).filter(User.id == user.id).delete()
    session.commit()
    session.close()


def test_parse_failure_surfaces_as_explicit_failure(pipeline_fixture) -> None:
    session, profile, scholarship, make_document = pipeline_fixture
    document = make_document()

    final_state = run_doc_pipeline(
        session,
        document=document,
        file_bytes=b"%PDF-1.4 this is not a real pdf body",
        filename="corrupt.pdf",
        declared_type="transcript",
        profile=profile,
        scholarship=scholarship,
    )

    assert final_state.get("error") is not None
    session.refresh(document)
    assert document.parsed_meta is None
    assert document.inconsistency_flags is not None
    assert any(flag["type"] == "parse_error" for flag in document.inconsistency_flags)


def test_scanned_pdf_is_needs_ocr_not_failure_or_empty(pipeline_fixture) -> None:
    session, profile, scholarship, make_document = pipeline_fixture
    document = make_document()

    final_state = run_doc_pipeline(
        session,
        document=document,
        file_bytes=_image_only_pdf_bytes(),
        filename="scan.pdf",
        declared_type="transcript",
        profile=profile,
        scholarship=scholarship,
    )

    assert final_state.get("error") is None
    assert final_state.get("needs_ocr") is True
    session.refresh(document)
    assert document.inconsistency_flags is not None
    assert any(flag["type"] == "needs_ocr" for flag in document.inconsistency_flags)
    assert not any(flag["type"] == "parse_error" for flag in document.inconsistency_flags)


def test_profile_document_inconsistency_is_flagged_not_resolved(pipeline_fixture) -> None:
    session, profile, scholarship, make_document = pipeline_fixture
    profile.education_records.append(EducationRecord(gpa=3.5, gpa_scale=4.0))
    session.commit()

    document = make_document()
    extracted = ExtractedDocumentFields(gpa=3.2, gpa_scale=4.0, value_status="known")

    with patch("app.workflows.doc_pipeline.graph.extract_document_fields", return_value=extracted):
        final_state = run_doc_pipeline(
            session,
            document=document,
            file_bytes=_text_pdf_bytes("GPA: 3.2/4.0"),
            filename="transcript.pdf",
            declared_type="transcript",
            profile=profile,
            scholarship=scholarship,
        )

    assert final_state.get("error") is None
    session.refresh(document)
    assert document.inconsistency_flags is not None
    mismatch = next(flag for flag in document.inconsistency_flags if flag["field"] == "gpa")
    assert mismatch["type"] == "profile_mismatch"
    assert mismatch["profile_value"] == 3.5
    assert mismatch["document_value"] == 3.2
    # Neither value is silently overwritten or dropped — the profile's
    # original GPA is untouched.
    session.refresh(profile)
    assert float(profile.education_records[0].gpa) == 3.5
