"""`api_connector` tool (Blueprint §11) — agent-facing wrapper over the
`api_connector` connector (T073): (source_id, query) -> normalized records +
provenance. The governance gate (active-source-only) lives in the connector;
this layer only shapes the connector's result into a schema-validated
contract. Importable/callable standalone — Discovery Agent wiring is Slice
2D, not here.
"""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.sources.connectors.api_connector import fetch_api

__all__ = ["ApiConnectorOutput", "api_connector_tool"]


class ApiConnectorOutput(BaseModel):
    source_id: uuid.UUID
    query: str
    status: str  # "ok" | "fail"
    records: list[dict[str, Any]]
    fetched_at: str | None = None
    error: str | None = None


def api_connector_tool(db: Session, source_id: uuid.UUID, query: str) -> ApiConnectorOutput:
    result = fetch_api(db, source_id, query)
    return ApiConnectorOutput(
        source_id=result.source_id,
        query=result.query,
        status="ok" if result.success else "fail",
        records=result.records,
        fetched_at=result.fetched_at.isoformat() if result.fetched_at else None,
        error=result.error,
    )
