"""Monitoring runs (002, data-model.md §4).

System-owned, no `user_id` — a monitoring run is not scoped to an end user
(same class as `source_registry`/`source_fetch_log`). Enums follow the 001
pattern: `native_enum=False`, stored by member NAME.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Integer, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class MonitoringTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class MonitoringStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[MonitoringTrigger] = mapped_column(
        Enum(MonitoringTrigger, name="monitoring_trigger", native_enum=False), nullable=False
    )
    status: Mapped[MonitoringStatus] = mapped_column(
        Enum(MonitoringStatus, name="monitoring_status", native_enum=False),
        nullable=False,
        default=MonitoringStatus.RUNNING,
    )
    sources_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sources_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changes_detected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
