"""Candidate-source validation history (002, data-model.md §7).

Append-only and purely advisory: never joined into any authoritative
scholarship path and never changes `candidate_sources.status` (FR-CANDVAL-2/3).
No `user_id` (governance data, same class as `candidate_sources`).
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class CandidateSourceValidation(Base):
    __tablename__ = "candidate_source_validations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidate_sources.id"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reachable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reachable_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    extractable_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_signals: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
