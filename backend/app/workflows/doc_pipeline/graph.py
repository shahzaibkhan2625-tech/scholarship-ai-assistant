"""`doc_pipeline` LangGraph workflow (T105, Blueprint) — `receive ->
identify_type -> parse -> extract_fields -> associate -> satisfy_check ->
flag_missing -> END`.

The caller (T108's `POST /applications/{id}/documents`) has already
durably persisted the uploaded bytes (storage) and the `application_documents`
row (`type=unclassified`, `parsed_meta=NULL`) *before* this workflow ever
runs — a failure here can never lose the user's file (Edge Cases).

**Three distinct terminal outcomes, never collapsed into two:**
1. A `PdfParseError` (corrupt, truncated, encrypted, or an unsupported file
   format) is an explicit pipeline failure — `state["error"]` is set,
   `parsed_meta` stays NULL (data-model.md §6), and the failure reason is
   recorded on `inconsistency_flags` so a later fetch can still show why
   (never relying on the HTTP response being the only record of it). The
   caller (T108) maps this to `422`.
2. A PDF that opens and parses fine but yields no extractable text (a
   scanned/image-only document, `ParsedDocument.is_empty`) is *not* a parse
   failure and is *not* "the document contains no information" — it is
   reported as needing OCR/a text-based copy via an `inconsistency_flags`
   entry, and the caller still returns `201`.
3. A successful parse with real text runs LLM-assisted field extraction
   (`extract_document_fields`), associates the document with any scholarship
   requirement its declared type can substantiate, and deterministically
   checks satisfaction (`requirement_satisfaction`, no LLM) plus profile
   inconsistencies — all NO-LLM comparisons, per constitution Principle II.

`parsed_meta` is populated only when extraction actually found at least one
informative field (`value_status == "known"`) — there is nothing useful to
persist otherwise, and leaving it NULL is the honest default (mirrors
`ingestion`'s "never guess" discipline)."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.data.repositories import document_repo
from app.models.document import ApplicationDocument, DocumentType
from app.models.requirement import Requirement, RequirementCategory
from app.services.requirement_satisfaction import (
    SatisfactionOutcome,
    SatisfactionStatus,
    detect_profile_inconsistencies,
    evaluate_requirements,
)
from app.tools.extract_document_fields import (
    DocumentFieldExtractionError,
    ExtractedDocumentFields,
    extract_document_fields,
)
from app.tools.pdf_parse import ParsedDocument, PdfParseError, parse_pdf

__all__ = ["DocPipelineState", "run_doc_pipeline"]

# A declared/uploaded document type only drives automated association when it
# maps to a requirement category we can deterministically compare against —
# an unmapped type is never guessed at (Edge Cases; FR-DOC-3).
_TYPE_TO_CATEGORIES: dict[DocumentType, list[RequirementCategory]] = {
    DocumentType.TRANSCRIPT: [RequirementCategory.GPA, RequirementCategory.ACADEMIC],
    DocumentType.DEGREE_CERTIFICATE: [RequirementCategory.ACADEMIC],
    DocumentType.LANGUAGE_TEST_DOC: [RequirementCategory.LANGUAGE],
    DocumentType.GRE_GMAT_DOC: [RequirementCategory.TEST],
}

_NEEDS_OCR_MESSAGE = (
    "This document appears to be a scanned or image-only file with no extractable text. "
    "Please upload a text-based copy or an OCR'd version."
)


class DocPipelineState(TypedDict, total=False):
    db: Any
    document: ApplicationDocument
    file_bytes: bytes
    filename: str
    declared_type: str
    profile: Any
    scholarship: Any
    parsed: ParsedDocument | None
    needs_ocr: bool
    extracted: ExtractedDocumentFields | None
    matching_requirements: list[Requirement]
    satisfaction_outcomes: list[SatisfactionOutcome]
    error: str | None


def _receive_node(state: DocPipelineState) -> dict:
    # The upload + DB row already exist by the time this workflow runs
    # (T108's ordering guarantee) — this node is the workflow's documented
    # entry point, not an additional persistence step.
    return {}


def _identify_type_node(state: DocPipelineState) -> dict:
    filename = state.get("filename") or ""
    if not filename.lower().endswith(".pdf"):
        return {"error": f"Unsupported file format for {filename!r}: only PDF files are currently supported."}
    return {}


def _parse_node(state: DocPipelineState) -> dict:
    if state.get("error"):
        return {}
    try:
        parsed = parse_pdf(state["file_bytes"])
    except PdfParseError as exc:
        return {"error": str(exc)}
    return {"parsed": parsed, "needs_ocr": parsed.is_empty}


def _extract_fields_node(state: DocPipelineState) -> dict:
    if state.get("error") or state.get("needs_ocr"):
        return {}
    parsed = state.get("parsed")
    if parsed is None or not parsed.text:
        return {}
    try:
        extracted = extract_document_fields(parsed.text)
    except DocumentFieldExtractionError:
        # Extraction failing is not a PdfParseError-grade pipeline failure —
        # the file itself parsed fine, so we proceed with "nothing extracted"
        # rather than blocking the upload.
        return {}
    return {"extracted": extracted}


def _associate_node(state: DocPipelineState) -> dict:
    if state.get("error"):
        return {}
    scholarship = state.get("scholarship")
    try:
        doc_type = DocumentType(state.get("declared_type"))
    except ValueError:
        doc_type = None

    categories = _TYPE_TO_CATEGORIES.get(doc_type, [])
    if scholarship is None or not categories:
        return {"matching_requirements": []}

    matching = [r for r in scholarship.requirements if r.category in categories]
    return {"matching_requirements": matching}


def _satisfy_check_node(state: DocPipelineState) -> dict:
    if state.get("error"):
        return {}
    extracted = state.get("extracted")
    matching_requirements = state.get("matching_requirements") or []
    if extracted is None or not matching_requirements:
        return {"satisfaction_outcomes": []}
    return {"satisfaction_outcomes": evaluate_requirements(extracted, matching_requirements)}


def _flag_missing_node(state: DocPipelineState) -> dict:
    """Terminal node: writes every observation this run produced back onto
    the `application_documents` row, exactly once. This is the only node
    that touches the DB — every earlier node is pure state transformation,
    so a run that errors partway through still reaches here and its failure
    reason is never lost."""
    document = state["document"]
    flags: list[dict] = []
    parsed_meta: dict | None = None

    if state.get("error"):
        flags.append({"type": "parse_error", "message": state["error"]})
    elif state.get("needs_ocr"):
        flags.append({"type": "needs_ocr", "message": _NEEDS_OCR_MESSAGE})
    else:
        extracted = state.get("extracted")
        if extracted is not None and extracted.value_status == "known":
            parsed_meta = extracted.model_dump()
            profile = state.get("profile")
            if profile is not None:
                flags.extend(flag.model_dump() for flag in detect_profile_inconsistencies(extracted, profile))

    satisfies_requirement_id = None
    for outcome in state.get("satisfaction_outcomes") or []:
        if outcome.status == SatisfactionStatus.SATISFIED:
            satisfies_requirement_id = outcome.requirement_id
            break

    document_repo.update_application_document(
        state["db"],
        document.user_id,
        document,
        parsed_meta=parsed_meta,
        satisfies_requirement_id=satisfies_requirement_id,
        inconsistency_flags=flags if flags else None,
    )
    return {}


def _build_graph():
    graph = StateGraph(DocPipelineState)
    graph.add_node("receive", _receive_node)
    graph.add_node("identify_type", _identify_type_node)
    graph.add_node("parse", _parse_node)
    graph.add_node("extract_fields", _extract_fields_node)
    graph.add_node("associate", _associate_node)
    graph.add_node("satisfy_check", _satisfy_check_node)
    graph.add_node("flag_missing", _flag_missing_node)

    graph.add_edge(START, "receive")
    graph.add_edge("receive", "identify_type")
    graph.add_edge("identify_type", "parse")
    graph.add_edge("parse", "extract_fields")
    graph.add_edge("extract_fields", "associate")
    graph.add_edge("associate", "satisfy_check")
    graph.add_edge("satisfy_check", "flag_missing")
    graph.add_edge("flag_missing", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_doc_pipeline(
    db,
    *,
    document: ApplicationDocument,
    file_bytes: bytes,
    filename: str,
    declared_type: str,
    profile: Any,
    scholarship: Any,
) -> DocPipelineState:
    initial_state: DocPipelineState = {
        "db": db,
        "document": document,
        "file_bytes": file_bytes,
        "filename": filename,
        "declared_type": declared_type,
        "profile": profile,
        "scholarship": scholarship,
    }
    return _COMPILED_GRAPH.invoke(initial_state)
