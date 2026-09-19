"""`pdf_parse` tool (T104, Blueprint §11 tool contract: `file -> {text, pages,
tables}`, PyMuPDF-backed).

Corrupt, truncated, or encrypted input is always a typed `PdfParseError` —
never a silently-empty `ParsedDocument`, never an untyped library exception.
A PDF that opens and parses fine but yields no extractable text (a scanned or
image-only document) is a *different*, non-error state: it comes back as a
`ParsedDocument` with `text=""` and `is_empty=True` so a later caller (T105's
`doc_pipeline`) can tell "nothing to extract" apart from "parsing failed"."""

import pymupdf
from pydantic import BaseModel

__all__ = ["ParsedDocument", "PdfParseError", "parse_pdf"]


class PdfParseError(Exception):
    """Raised when a PDF cannot be opened or read — corrupt, truncated, or
    encrypted (password-protected) input."""


class ParsedDocument(BaseModel):
    text: str
    pages: int
    tables: list[list[list[str]]] = []
    is_empty: bool = False


def parse_pdf(data: bytes) -> ParsedDocument:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise PdfParseError(f"Could not open PDF: {exc}") from exc

    try:
        if doc.is_encrypted:
            raise PdfParseError("PDF is encrypted/password-protected and cannot be parsed")

        page_count = doc.page_count
        text_parts: list[str] = []
        tables: list[list[list[str]]] = []
        try:
            for page in doc:
                text_parts.append(page.get_text())
                for table in page.find_tables().tables:
                    tables.append(table.extract())
        except Exception as exc:
            raise PdfParseError(f"Could not extract content from PDF: {exc}") from exc
    finally:
        doc.close()

    text = "".join(text_parts).strip()
    return ParsedDocument(text=text, pages=page_count, tables=tables, is_empty=not text)
