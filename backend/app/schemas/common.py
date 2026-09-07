"""Shared value-status / confidence vocabulary (constitution Principle I).

Every scholarship field, requirement, funding detail, matching criterion, and
Q&A answer in this codebase carries these labels so "we don't know" is always
representable and is never silently defaulted or guessed.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ValueStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    CONDITIONAL = "conditional"
    CONFLICTING = "conflicting"


class Confidence(StrEnum):
    VERIFIED = "verified"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class FieldValue(BaseModel):
    """Generic carrier for any fact that may be missing, inferred, or
    conflicting — never a bare value on its own (FR-INTEL-1/2/3)."""

    model_config = ConfigDict(frozen=True)

    value: object | None = None
    value_status: ValueStatus
    confidence: Confidence
    evidence_snippet: str | None = None
    source_url: str | None = None
    last_verified_at: datetime | None = None
