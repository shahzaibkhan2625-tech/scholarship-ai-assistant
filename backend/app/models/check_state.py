"""Per-source runtime state for change detection, cadence back-off and rule review (003, data-model.md)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, false, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class SourceCheckState(Base):
    __tablename__ = "source_check_state"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_registry.id"), primary_key=True
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(Text, nullable=True)
    validators_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    last_extraction_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_full_extraction_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backoff_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checks_window: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    changed_window: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rules_review_needed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    rules_review_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
