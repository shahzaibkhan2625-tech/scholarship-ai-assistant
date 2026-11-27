"""Monitoring run request/response schemas (002, data-model.md §4)."""

import uuid
from datetime import date, datetime
from typing import Literal

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
    # 003 FR-OBS-3: checks skipped as unchanged / deferred for budget or pause (0 on older rows).
    sources_skipped: int = 0
    sources_deferred: int = 0


class MonitoringRunDetail(MonitoringRunRead):
    # Fetch-log rows that carry this run's id (one per fetch attempt outcome).
    source_outcomes: list[SourceFetchLogRead] = Field(default_factory=list)


class BudgetRequestType(BaseModel):
    """One request type's day (003 FR-OBS-5). `allowance` / `remaining` are null when counted, not limited."""

    allowance: int | None = None
    reserve: int | None = None
    used: dict[str, int]
    remaining: int | None = None
    deferred: int = 0
    denied: int = 0
    provider_exhausted: int = 0
    discrepancy: int = 0
    avoided_unchanged: int = 0
    avoided_rules: int = 0
    paused_until: datetime | None = None
    observed_limit: int | None = None


class BudgetSource(BaseModel):
    domain: str
    interval_minutes: int
    interval_source: Literal["derived", "override", "default"]
    last_checked_at: datetime | None = None
    next_due_at: datetime | None = None
    noisy: bool = False
    rules_review_needed: bool = False


class BudgetView(BaseModel):
    budget_day: date
    reset_at: datetime
    classify_pending: int
    request_types: dict[str, BudgetRequestType]
    sources: list[BudgetSource]
