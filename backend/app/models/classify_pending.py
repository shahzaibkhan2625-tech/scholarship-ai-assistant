"""Pending-classification queue (003 FR-BUDGET-13, data-model.md).

One row per record whose classify LLM call was refused or failed. A separate table, not a column
on scholarships, so what a record means stays feature 004's matter (ADR-0013). `last_reason` is a
reason class, never provider text.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class ClassifyPending(Base):
    __tablename__ = "classify_pending"

    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), primary_key=True
    )
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
