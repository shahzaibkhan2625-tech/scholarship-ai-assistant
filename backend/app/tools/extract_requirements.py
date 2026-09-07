"""`extract_requirements` tool — LLM-assisted extraction of a scholarship's
requirements/funding/deadline from a fetched official page. Every extracted
field carries an explicit value_status/confidence — the LLM is instructed to
say "unknown" rather than guess, per constitution Principle I. This tool only
extracts; it never decides eligibility (that stays in the deterministic
hard_constraints service, per Principle II)."""

from pydantic import BaseModel, Field

from app.tools._json_utils import extract_json_object

_SYSTEM_INSTRUCTION = """You extract structured scholarship data from official scholarship-page text.
Respond with ONLY a JSON object (no markdown, no commentary) matching exactly this shape:
{
  "name": string,
  "provider": string or null,
  "country": string or null,
  "field": string or null,
  "degree_level": one of "BS", "MS", "PhD", "other", or null,
  "funding_status": one of "fully_funded", "substantially_funded", "partially_funded",
                    "tuition_only", "stipend_only", "other_combination", "unknown",
  "official_application_url": string or null,
  "deadline": an ISO date string (YYYY-MM-DD) or null,
  "requirements": [
    {
      "category": one of "eligibility", "academic", "gpa", "language", "test", "age",
                  "experience", "nationality", "research", "other",
      "key": string,
      "value": the requirement's value (string, number, or null),
      "mandatory": boolean,
      "value_status": one of "known", "unknown", "not_applicable", "conditional", "conflicting",
      "confidence": one of "verified", "inferred", "unknown"
    }
  ],
  "funding_details": {
    "tuition_coverage": string or null,
    "stipend": string or null,
    "accommodation": string or null,
    "travel_airfare": string or null,
    "health_insurance": string or null,
    "visa_support": string or null,
    "spouse_allowed": boolean or null,
    "value_status": one of "known", "unknown", "not_applicable", "conditional", "conflicting",
    "confidence": one of "verified", "inferred", "unknown"
  }
}
If the page text does not state a field, use null and value_status "unknown" — never invent a
value. "value_status" MUST be "known" only when the page text explicitly states that value."""


class ExtractedRequirement(BaseModel):
    category: str = "other"
    key: str
    value: object = None
    mandatory: bool = True
    value_status: str = "unknown"
    confidence: str = "unknown"


class ExtractedFundingDetails(BaseModel):
    tuition_coverage: str | None = None
    tuition_amount_or_pct: str | None = None
    stipend: str | None = None
    accommodation: str | None = None
    travel_airfare: str | None = None
    health_insurance: str | None = None
    visa_support: str | None = None
    family_dependent_benefits: str | None = None
    spouse_allowed: bool | None = None
    dependent_policy: str | None = None
    value_status: str = "unknown"
    confidence: str = "unknown"


class ExtractedScholarshipData(BaseModel):
    name: str
    provider: str | None = None
    country: str | None = None
    field: str | None = None
    degree_level: str | None = None
    funding_status: str = "unknown"
    official_application_url: str | None = None
    deadline: str | None = None
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    funding_details: ExtractedFundingDetails | None = None


def extract_requirements_from_page(page_text: str, *, source_url: str) -> ExtractedScholarshipData:
    from app.core.llm import generate_text

    prompt = f"Official scholarship page (source: {source_url}):\n\n{page_text[:20000]}"
    raw = generate_text(prompt, system_instruction=_SYSTEM_INSTRUCTION)
    parsed = extract_json_object(raw)
    return ExtractedScholarshipData(**parsed)
