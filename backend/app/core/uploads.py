"""Bounded, content-based reading of uploaded files (010 US5, FR-UPLOAD-1, -2).

Both helpers read at most `max_bytes + 1` bytes, so an oversized upload is refused without being
held in memory, and a file of exactly `max_bytes` is accepted. The kind of file is decided by its
bytes, never by its name or declared content type. The errors are `HTTPException`s, so a route
only has to call the helper before it stores or parses anything.
"""

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings

__all__ = [
    "UploadError",
    "FileTooLarge",
    "EmptyFile",
    "NotAPdf",
    "UnsupportedCv",
    "is_pdf",
    "read_pdf_upload",
    "read_cv_upload",
]

PDF_MAGIC = b"%PDF-"
PDF_HEADER_WINDOW = 1024


class UploadError(HTTPException):
    status_code_value = status.HTTP_400_BAD_REQUEST
    message = ""

    def __init__(self) -> None:
        super().__init__(status_code=self.status_code_value, detail=self.message)


class FileTooLarge(UploadError):
    status_code_value = 413
    message = "File too large (maximum 10 MB)"


class EmptyFile(UploadError):
    status_code_value = status.HTTP_400_BAD_REQUEST
    message = "File is empty"


class NotAPdf(UploadError):
    status_code_value = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    message = "Only PDF files are accepted"


class UnsupportedCv(UploadError):
    status_code_value = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    message = "Unsupported file: send a PDF or a UTF-8 plain-text file"


def is_pdf(data: bytes) -> bool:
    """A PDF has `%PDF-` within its first 1024 bytes (the PDF convention; the name is ignored)."""
    return PDF_MAGIC in data[:PDF_HEADER_WINDOW]


def _read_bounded(file: UploadFile, max_bytes: int) -> bytes:
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FileTooLarge()
    if not data:
        raise EmptyFile()
    return data


def read_pdf_upload(file: UploadFile, max_bytes: int = settings.max_upload_bytes) -> bytes:
    """Document upload: the bytes of a PDF, else 413 / 400 / 415."""
    data = _read_bounded(file, max_bytes)
    if not is_pdf(data):
        raise NotAPdf()
    return data


def read_cv_upload(file: UploadFile, max_bytes: int = settings.max_upload_bytes) -> bytes:
    """CV import: a PDF by content, or strict UTF-8 text with no NUL byte (a NUL means binary)."""
    data = _read_bounded(file, max_bytes)
    if is_pdf(data):
        return data
    if b"\x00" in data:
        raise UnsupportedCv()
    try:
        data.decode("utf-8")  # strict decode: invalid bytes are refused, never dropped
    except UnicodeDecodeError:
        raise UnsupportedCv() from None
    return data
