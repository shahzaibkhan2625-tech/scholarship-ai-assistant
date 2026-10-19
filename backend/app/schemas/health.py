"""Source-health response schemas (002 US3, FR-HEALTH-3, contracts `SourceHealth` /
`SourceFetchLogEntry`).

`status` is a three-value Literal on purpose: `pending` registry rows are not
part of the health view (ADR-0008), and the contract enum stays
`[active, failing, disabled]`. There is deliberately no "items found"/results
field on `SourceHealth` — a failed fetch must not be expressible as a zero."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.source import SourceFetchLogRead

HealthStatus = Literal["active", "failing", "disabled"]


class SourceHealth(BaseModel):
    source_id: uuid.UUID
    name: str
    status: HealthStatus
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int
    listing_page_url: str | None = None


class SourceFetchLogEntry(SourceFetchLogRead):
    """Contract name for a fetch-log row; same fields as `SourceFetchLogRead`."""
