"""`classify` tool (T077, Blueprint §11, §31) — rules-based keyword/pattern
matching first pass for degree_level, funding_type, provider_type, country,
and field, falling back to an LLM tool call only for dimensions the rules
can't resolve. Output is always a typed enum value (never free text) plus a
verified/inferred/unknown confidence (§14 confidence discipline).

Per §14/§32, `confidence="verified"` is reserved exclusively for
official-source-confirmed data (computed downstream in
`app/services/verification.py`) — this tool never assigns it. A deterministic
rules match is still only a guess about *meaning* extracted from text, not an
official confirmation, so both a rules match and an LLM fallback match are
`inferred` (rules simply being the higher-precision inference path); a
dimension neither rules nor the LLM can resolve stays `unknown` with
`value=None` — never guessed (constitution Principle I).
"""

import re

from pydantic import BaseModel

from app.models.scholarship import DegreeLevel, FundingStatus
from app.models.source import SourceType
from app.tools._json_utils import extract_json_object

__all__ = ["ClassificationField", "ClassificationResult", "classify"]


class ClassificationField(BaseModel):
    value: str | None = None
    confidence: str = "unknown"  # verified | inferred | unknown


class ClassificationResult(BaseModel):
    degree_level: ClassificationField = ClassificationField()
    funding_type: ClassificationField = ClassificationField()
    provider_type: ClassificationField = ClassificationField()
    country: ClassificationField = ClassificationField()
    field: ClassificationField = ClassificationField()


_DEGREE_PATTERNS: dict[DegreeLevel, list[str]] = {
    DegreeLevel.PHD: [r"\bphd\b", r"\bph\.d\b", r"\bdoctoral\b", r"\bdoctorate\b"],
    DegreeLevel.MS: [r"\bmaster'?s?\b", r"\bmsc\b", r"\bm\.sc\b", r"\bgraduate program\b"],
    DegreeLevel.BS: [r"\bbachelor'?s?\b", r"\bundergraduate\b", r"\bbsc\b", r"\bb\.sc\b"],
}

_FUNDING_PATTERNS: dict[FundingStatus, list[str]] = {
    FundingStatus.FULLY_FUNDED: [r"\bfully[- ]funded\b", r"\bfull scholarship\b", r"\ball expenses (covered|paid)\b"],
    FundingStatus.TUITION_ONLY: [r"\btuition[- ]only\b", r"\btuition waiver\b"],
    FundingStatus.STIPEND_ONLY: [r"\bstipend[- ]only\b"],
    FundingStatus.SUBSTANTIALLY_FUNDED: [r"\bsubstantially[- ]funded\b", r"\bmost expenses\b"],
    FundingStatus.PARTIALLY_FUNDED: [r"\bpartial(ly)?[- ]funded\b", r"\bpartial scholarship\b"],
}

_PROVIDER_PATTERNS: dict[SourceType, list[str]] = {
    SourceType.GOV: [r"\bministry\b", r"\bgovernment\b", r"\bgov\.\w+\b"],
    SourceType.EMBASSY: [r"\bembassy\b"],
    SourceType.INTERNATIONAL_ORG: [r"\bunited nations\b", r"\bunesco\b", r"\bworld bank\b"],
    SourceType.UNIVERSITY: [r"\buniversity\b", r"\bcollege\b"],
    SourceType.FOUNDATION: [r"\bfoundation\b"],
    SourceType.NGO: [r"\bngo\b", r"\bnon-?profit\b"],
}

# Small canonical, expandable set (rules pass only — an unmatched country
# always falls through to the LLM fallback rather than being force-fit).
_KNOWN_COUNTRIES = [
    "germany", "pakistan", "hungary", "saudi arabia", "united states", "usa",
    "united kingdom", "uk", "china", "japan", "south korea", "france", "italy",
    "spain", "canada", "australia", "netherlands", "sweden", "norway", "finland",
    "denmark", "switzerland", "austria", "belgium", "turkey", "india", "malaysia",
    "singapore", "new zealand", "ireland", "poland", "czech republic",
]

_KNOWN_FIELDS = [
    "computer science", "engineering", "medicine", "law", "business",
    "economics", "social sciences", "natural sciences", "physics", "chemistry",
    "biology", "mathematics", "arts", "humanities", "education", "public health",
    "environmental science", "political science", "psychology", "architecture",
]


def _match_enum_patterns(text_lower: str, patterns: dict) -> ClassificationField:
    # "inferred", not "verified": a deterministic text match still isn't an
    # official-source confirmation (§14/§32) — see module docstring.
    for value, regexes in patterns.items():
        if any(re.search(rx, text_lower) for rx in regexes):
            return ClassificationField(value=value.value, confidence="inferred")
    return ClassificationField(value=None, confidence="unknown")


def _match_known_list(text_lower: str, known_values: list[str]) -> ClassificationField:
    for candidate in known_values:
        if candidate in text_lower:
            return ClassificationField(value=candidate, confidence="inferred")
    return ClassificationField(value=None, confidence="unknown")


_LLM_SYSTEM_INSTRUCTION = """You classify a scholarship description into a fixed set of
dimensions. Respond with ONLY a JSON object (no markdown, no commentary) matching exactly:
{
  "degree_level": one of "BS", "MS", "PhD", "other", or null,
  "funding_type": one of "fully_funded", "substantially_funded", "partially_funded",
                  "tuition_only", "stipend_only", "other_combination", or null,
  "provider_type": one of "gov", "national_education", "university", "department",
                    "provider", "foundation", "ngo", "embassy", "international_org",
                    "research", "api", "approved_aggregator", or null,
  "country": a country name (string) or null,
  "field": a field of study (string) or null
}
Only requested dimensions need a non-null value; if the text does not clearly indicate a
dimension, use null rather than guessing (never invent a value)."""


def _llm_fallback_classify(text: str, dimensions: list[str]) -> dict:
    from app.core.llm import generate_text

    prompt = f"Requested dimensions: {', '.join(dimensions)}\n\nScholarship description:\n\n{text[:8000]}"
    raw = generate_text(prompt, system_instruction=_LLM_SYSTEM_INSTRUCTION)
    return extract_json_object(raw)


def classify(text: str) -> ClassificationResult:
    """Rules-based first pass over `text` for all five dimensions; any
    dimension the rules can't resolve is deferred to a single LLM fallback
    call covering only the unresolved dimensions."""

    text_lower = text.lower()
    results: dict[str, ClassificationField] = {
        "degree_level": _match_enum_patterns(text_lower, _DEGREE_PATTERNS),
        "funding_type": _match_enum_patterns(text_lower, _FUNDING_PATTERNS),
        "provider_type": _match_enum_patterns(text_lower, _PROVIDER_PATTERNS),
        "country": _match_known_list(text_lower, _KNOWN_COUNTRIES),
        "field": _match_known_list(text_lower, _KNOWN_FIELDS),
    }

    unresolved = [dim for dim, field_result in results.items() if field_result.value is None]
    if unresolved:
        try:
            llm_result = _llm_fallback_classify(text, unresolved)
        except Exception:
            llm_result = {}
        for dim in unresolved:
            value = llm_result.get(dim)
            if value:
                results[dim] = ClassificationField(value=str(value), confidence="inferred")

    return ClassificationResult(**results)
