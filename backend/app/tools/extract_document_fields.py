"""`extract_document_fields` tool (T105 supporting piece) — LLM-assisted
structured extraction of academic facts (GPA, degree, institution, graduation
date, test scores) from a parsed document's text. Mirrors
`extract_requirements`'s pattern: the LLM is told to report "unknown" rather
than guess, and the result carries an explicit value_status/confidence. This
tool only extracts; it never decides whether a value satisfies a
requirement — that stays in the deterministic `requirement_satisfaction`
service (constitution Principle II).

Unlike `extract_requirements`, invalid/unparseable model output is not just
raised to the caller — it is rejected and the LLM is asked again, up to a
small fixed retry budget, before an explicit `DocumentFieldExtractionError`
is raised (never a silently-empty result passed off as "nothing found")."""

from pydantic import BaseModel, Field, ValidationError

from app.tools._json_utils import ExtractionParseError, extract_json_object

_SYSTEM_INSTRUCTION = """You extract structured academic facts from a student's uploaded document text
(transcript, degree certificate, test-score report, etc.). Respond with ONLY a JSON object (no
markdown, no commentary) matching exactly this shape:
{
  "gpa": number or null,
  "gpa_scale": number or null,
  "degree": string or null,
  "institution": string or null,
  "graduation_date": an ISO date string (YYYY-MM-DD) or null,
  "test_scores": [
    {"test_type": one of "IELTS", "TOEFL", "PTE", "GRE", "GMAT", "other", "score": number or null}
  ],
  "value_status": one of "known", "unknown", "not_applicable", "conditional", "conflicting",
  "confidence": one of "verified", "inferred", "unknown"
}
If the document text does not state a field, use null — never invent a value. "value_status" MUST
be "known" only when the document text explicitly states at least one of these fields."""

DEFAULT_MAX_ATTEMPTS = 2


class ExtractedTestScore(BaseModel):
    test_type: str
    score: float | None = None


class ExtractedDocumentFields(BaseModel):
    gpa: float | None = None
    gpa_scale: float | None = None
    degree: str | None = None
    institution: str | None = None
    graduation_date: str | None = None
    test_scores: list[ExtractedTestScore] = Field(default_factory=list)
    value_status: str = "unknown"
    confidence: str = "unknown"


class DocumentFieldExtractionError(Exception):
    """Raised when the LLM fails to produce schema-valid JSON after the
    reject-and-retry budget is exhausted."""


def extract_document_fields(
    document_text: str, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> ExtractedDocumentFields:
    from app.core.llm import generate_text

    prompt = f"Uploaded document text:\n\n{document_text[:20000]}"
    last_error: Exception | None = None

    for _ in range(max_attempts):
        raw = generate_text(prompt, system_instruction=_SYSTEM_INSTRUCTION)
        try:
            parsed = extract_json_object(raw)
            return ExtractedDocumentFields(**parsed)
        except (ExtractionParseError, ValidationError) as exc:
            last_error = exc
            continue

    raise DocumentFieldExtractionError(
        f"Could not extract valid document fields after {max_attempts} attempt(s): {last_error}"
    )
