"""010 T395: upload helpers read a bounded amount and decide by content (FR-UPLOAD-1, -2)."""

import io

import pytest
from fastapi import UploadFile

from app.core.uploads import read_cv_upload, read_pdf_upload

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"
CV_MESSAGE = "Unsupported file: send a PDF or a UTF-8 plain-text file"


class _SpyFile(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.requested: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested.append(size)
        return super().read(size)


def _upload(data: bytes, name: str = "f.bin") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


def _refused(reader, data: bytes, status: int, detail: str, max_bytes: int = 100, name: str = "f.bin") -> None:
    with pytest.raises(Exception) as caught:
        reader(_upload(data, name), max_bytes)
    assert caught.value.status_code == status
    assert caught.value.detail == detail


@pytest.mark.parametrize("reader", [read_pdf_upload, read_cv_upload])
def test_reads_at_most_max_bytes_plus_one(reader):
    spy = _SpyFile(PDF + b"x" * 500)
    with pytest.raises(Exception):
        reader(UploadFile(file=spy, filename="a.pdf"), 100)
    assert spy.requested == [101]


@pytest.mark.parametrize("reader", [read_pdf_upload, read_cv_upload])
def test_exact_limit_is_accepted_and_one_more_byte_is_413(reader):
    exact = PDF + b" " * (100 - len(PDF))
    assert reader(_upload(exact), 100) == exact
    _refused(reader, exact + b" ", 413, "File too large (maximum 10 MB)")


@pytest.mark.parametrize("reader", [read_pdf_upload, read_cv_upload])
def test_empty_file_is_400(reader):
    _refused(reader, b"", 400, "File is empty")


def test_pdf_header_within_first_1024_bytes_is_enough():
    data = b"junk" * 100 + PDF  # header at byte 400
    assert read_pdf_upload(_upload(data, "x.bin"), 10_000) == data


def test_pdf_header_after_1024_bytes_is_not_a_pdf():
    _refused(read_pdf_upload, b"a" * 1100 + PDF, 415, "Only PDF files are accepted", 10_000)


def test_document_route_refuses_text_even_when_named_pdf():
    _refused(read_pdf_upload, b"just text", 415, "Only PDF files are accepted", name="x.pdf")


def test_cv_accepts_pdf_by_content_under_any_name():
    assert read_cv_upload(_upload(PDF, "cv.bin"), 100) == PDF


def test_cv_accepts_strict_utf_8_text_under_any_name():
    text = "José Zoë — résumé".encode("utf-8")
    assert read_cv_upload(_upload(text, "cv.pdf"), 100) == text


@pytest.mark.parametrize(
    "data",
    [b"caf\xe9", b"\x89PNG\r\n\x1a\n\x00\x00", b"hello\x00world"],
    ids=["latin1", "png", "nul"],
)
def test_cv_refuses_invalid_utf_8_binary_and_nul(data):
    _refused(read_cv_upload, data, 415, CV_MESSAGE)
