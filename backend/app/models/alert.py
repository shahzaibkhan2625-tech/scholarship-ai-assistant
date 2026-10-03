"""Alerts and per-user alert preferences (002, data-model.md §5–6).

User-owned: both tables carry `user_id` directly (constitution §19).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, true, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, index=True
    )
    match_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id"), nullable=True
    )
    monitoring_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("monitoring_runs.id"), nullable=True
    )
    change_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    channels_requested: Mapped[list] = mapped_column(JSONB, nullable=False)
    delivered_in_app_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_email_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # O-1: set only by mark-read (T250); NULL means unread. No default.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AlertPreferences(Base):
    """One row per user; an absent row means "defaults" (both channels on)."""

    __tablename__ = "alert_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    in_app_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
