"""`listing_fetch` tool (T135) — agent-facing wrapper over the `official_fetch`
connector's `fetch_and_extract_listing` (T135): (source_id, url) -> {status,
candidates (fresh extraction) | cached (already-persisted scholarships),
error}. The governance gate (active-source-only) and the two-layer
extraction cache (T135/A9) both live in the connector; this layer only
shapes the connector's result into a schema-validated contract — exactly
like `official_fetch_tool`/`api_connector_tool` already do for their own
connectors. Importable/callable standalone; Discovery Agent wiring only ever
calls this tool, never the connector directly (see
`sources/connectors/official_fetch.py`'s module docstring)."""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.source import VerificationStatus
from app.sources.connectors.official_fetch import fetch_and_extract_listing

__all__ = ["CachedScholarshipRefOutput", "ListingFetchOutput", "listing_fetch_tool"]


class CachedScholarshipRefOutput(BaseModel):
    scholarship_id: uuid.UUID
    verification_status: VerificationStatus


class ListingFetchOutput(BaseModel):
    source_id: uuid.UUID
    status: str  # "ok" | "fail" | "cached"
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    cached: list[CachedScholarshipRefOutput] = Field(default_factory=list)
    error: str | None = None


def listing_fetch_tool(db: Session, source_id: uuid.UUID, url: str | None = None) -> ListingFetchOutput:
    """`url=None` lets the connector resolve the fetch target (T165)."""
    result = fetch_and_extract_listing(db, source_id, url)
    return ListingFetchOutput(
        source_id=result.source_id,
        status=result.status,
        candidates=result.candidates,
        cached=[
            CachedScholarshipRefOutput(scholarship_id=ref.scholarship_id, verification_status=ref.verification_status)
            for ref in result.cached
        ],
        error=result.error,
    )
