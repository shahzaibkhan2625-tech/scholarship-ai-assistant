import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class EligibilityVerdict(StrEnum):
    ELIGIBLE = "eligible"
    LIKELY = "likely"
    POSSIBLY = "possibly"
    NOT = "not"
    UNKNOWN_REQUIRES_VERIFICATION = "unknown_requires_verification"


class MatchStrength(StrEnum):
    STRONG = "strong"
    POSSIBLE = "possible"
    NOT = "not"


class Match(Base):
    """Immutable point-in-time verdict — a `matches` row is never updated;
    re-matching produces a new row (history preserved for the tracker)."""

    __tablename__ = "matches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, index=True
    )
    eligibility_verdict: Mapped[EligibilityVerdict] = mapped_column(
        Enum(EligibilityVerdict, name="eligibility_verdict", native_enum=False), nullable=False
    )
    match_strength: Mapped[MatchStrength] = mapped_column(
        Enum(MatchStrength, name="match_strength", native_enum=False), nullable=False
    )
    hard_constraints: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    soft_preferences: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    exclusions_triggered: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    matched_criteria: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    failed_criteria: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    missing_information: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    unverified_criteria: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    required_documents: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    remaining_actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
