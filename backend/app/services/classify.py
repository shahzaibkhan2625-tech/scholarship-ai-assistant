"""`classify` service (T080, Blueprint §31) — dispatches to the `classify`
tool (T077) for a candidate record's descriptive text and maps the tool's
typed output onto the scholarship's classification dimensions
(degree_level, funding_type, provider_type, country, field). `degree_level`,
`funding_type` (-> `Scholarship.funding_status`), `country`, and `field` map
onto first-class `Scholarship` columns; `provider_type` has no first-class
column (§14) and is carried separately for the caller to store as a
`scholarship_fields` row.
"""

from dataclasses import dataclass

from app.tools.classify import ClassificationResult
from app.tools.classify import classify as classify_tool

__all__ = ["ClassifiedScholarshipFields", "classify_candidate"]


@dataclass(frozen=True)
class ClassifiedScholarshipFields:
    degree_level: str | None
    degree_level_confidence: str
    funding_status: str | None
    funding_status_confidence: str
    provider_type: str | None
    provider_type_confidence: str
    country: str | None
    country_confidence: str
    field: str | None
    field_confidence: str


def classify_candidate(text: str) -> ClassifiedScholarshipFields:
    """Runs the classify tool once over `text` and reshapes its
    per-dimension `ClassificationField`s into the flat, scholarship-facing
    shape the ingestion pipeline's store step consumes."""

    result: ClassificationResult = classify_tool(text)
    return ClassifiedScholarshipFields(
        degree_level=result.degree_level.value,
        degree_level_confidence=result.degree_level.confidence,
        funding_status=result.funding_type.value,
        funding_status_confidence=result.funding_type.confidence,
        provider_type=result.provider_type.value,
        provider_type_confidence=result.provider_type.confidence,
        country=result.country.value,
        country_confidence=result.country.confidence,
        field=result.field.value,
        field_confidence=result.field.confidence,
    )
