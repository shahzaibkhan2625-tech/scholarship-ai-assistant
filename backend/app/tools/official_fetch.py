"""`official_fetch` tool (Blueprint §11) — agent-facing wrapper over the
`official_fetch` connector (T073): (source_id, url) -> {content, status,
fetched_at, source_url}. The governance gate (active-source-only) lives in
the connector; this layer only shapes the connector's result into a
schema-validated contract. Importable/callable standalone — Discovery Agent
wiring is Slice 2D, not here.
"""

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.sources.connectors.official_fetch import fetch_official

__all__ = ["OfficialFetchOutput", "official_fetch_tool"]


class OfficialFetchOutput(BaseModel):
    source_id: uuid.UUID
    source_url: str
    status: str  # "ok" | "fail"
    content: str | None = None
    fetched_at: str | None = None
    error: str | None = None


def official_fetch_tool(db: Session, source_id: uuid.UUID, url: str) -> OfficialFetchOutput:
    result = fetch_official(db, source_id, url)
    return OfficialFetchOutput(
        source_id=result.source_id,
        source_url=result.source_url,
        status="ok" if result.success else "fail",
        content=result.content,
        fetched_at=result.fetched_at.isoformat() if result.fetched_at else None,
        error=result.error,
    )
