"""Discovery Agent schemas (T088, Blueprint §7.3, §30.4; contracts/openapi.yaml
`DiscoveryResult`/`CoverageSummary`). Field names/shapes match the OpenAPI
contract exactly so a later API-wiring slice (T090) can serialize these
directly; this module reuses `app.models.source`'s enums (T069) rather than
redefining source-type/verification vocabulary.

`CoverageSummary.claims_complete_coverage` is typed `Literal[False]` — not
just documented as false — so nothing downstream can accidentally construct a
coverage summary that claims completeness (Blueprint §30.4 / PRD B3: coverage
is measured, never asserted complete).
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.source import VerificationStatus

__all__ = ["CoverageSummary", "DiscoveryResultItem", "DiscoveryResult"]


class CoverageSummary(BaseModel):
    """Derived from `source_registry` + `source_fetch_log` (services/coverage.py,
    T084) — pure measurement, never a completeness claim."""

    model_config = ConfigDict(frozen=True)

    as_of: datetime
    sources_configured: int
    sources_active: int
    sources_checked: int
    sources_failed: int
    # source_id (str) -> ISO timestamp of that source's most recent fetch attempt.
    last_checked_at: dict[str, datetime] = {}
    # Human-readable "<country> / <source_type>: <reason>" entries — every
    # (country, source_type) combination configured anywhere in the registry
    # that currently has no active, non-failing source covering it.
    countries_covered: list[str] = []
    gaps: list[str] = []
    claims_complete_coverage: Literal[False] = False


class DiscoveryResultItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    scholarship_id: uuid.UUID
    source_id: uuid.UUID
    source_type: str
    verification_status: VerificationStatus


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    results: list[DiscoveryResultItem] = []
    coverage: CoverageSummary
