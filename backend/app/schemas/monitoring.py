"""Monitoring run request/response schemas (002, data-model.md §4)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.monitoring import MonitoringStatus, MonitoringTrigger
from app.schemas.source import SourceFetchLogRead


class MonitoringRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None = None
    trigger: MonitoringTrigger
    status: MonitoringStatus
    sources_processed: int
    sources_failed: int
    changes_detected: int


class MonitoringRunDetail(MonitoringRunRead):
    # Fetch-log rows that carry this run's id (one per fetch attempt outcome).
    source_outcomes: list[SourceFetchLogRead] = Field(default_factory=list)
