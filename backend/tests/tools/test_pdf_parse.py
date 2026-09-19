"""`pdf_parse` tool tests (T104). Fixture PDFs are built in-memory with
PyMuPDF itself rather than checked-in binary fixtures, so the test suite has
no external test-data dependency."""

import pymupdf
import pytest

from app.tools.pdf_parse import PdfParseError, parse_pdf


def _text_pdf_bytes(text: str = "Hello world sample transcript text") -> bytes:
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


def _encrypted_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "secret text")
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()
    return data


def test_valid_pdf_returns_extracted_text():
    result = parse_pdf(_text_pdf_bytes("Hello world sample transcript text"))

    assert "Hello world sample transcript text" in result.text
    assert result.pages == 1
    assert result.is_empty is False


def test_corrupt_bytes_raise_pdf_parse_error():
    with pytest.raises(PdfParseError):
        parse_pdf(b"this is not a pdf at all")


def test_truncated_bytes_raise_pdf_parse_error():
    valid = _text_pdf_bytes()
    truncated = valid[: len(valid) // 2]

    with pytest.raises(PdfParseError):
        parse_pdf(truncated)


def test_encrypted_pdf_raises_pdf_parse_error():
    with pytest.raises(PdfParseError):
        parse_pdf(_encrypted_pdf_bytes())


def test_image_only_pdf_returns_empty_flag_not_an_error():
    result = parse_pdf(_image_only_pdf_bytes())

    assert result.text == ""
    assert result.is_empty is True
    assert result.pages == 1
