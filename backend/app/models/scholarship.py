import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.data.repositories.db import Base


class DegreeLevel(StrEnum):
    BS = "BS"
    MS = "MS"
    PHD = "PhD"
    OTHER = "other"


class FundingStatus(StrEnum):
    """Never a binary flag, never a universal % threshold — FR-INTEL-5."""

    FULLY_FUNDED = "fully_funded"
    SUBSTANTIALLY_FUNDED = "substantially_funded"
    PARTIALLY_FUNDED = "partially_funded"
    TUITION_ONLY = "tuition_only"
    STIPEND_ONLY = "stipend_only"
    OTHER_COMBINATION = "other_combination"
    UNKNOWN = "unknown"


class LifecycleStatus(StrEnum):
    NEWLY_DISCOVERED = "newly_discovered"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UPDATED = "updated"
    EXPIRED = "expired"
    CLOSED = "closed"
    REOPENED = "reopened"
    STALE = "stale"
    SOURCE_UNAVAILABLE = "source_unavailable"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"


class University(Base):
    __tablename__ = "universities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str | None] = mapped_column(String, nullable=True)


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    university_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universities.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    duration: Mapped[str | None] = mapped_column(String, nullable=True)
    intake: Mapped[str | None] = mapped_column(String, nullable=True)


class Scholarship(Base):
    __tablename__ = "scholarships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    university_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("universities.id"), nullable=True
    )
    program_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=True
    )
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    degree_level: Mapped[DegreeLevel | None] = mapped_column(
        Enum(DegreeLevel, name="scholarship_degree_level", native_enum=False), nullable=True
    )
    funding_status: Mapped[FundingStatus] = mapped_column(
        Enum(FundingStatus, name="funding_status", native_enum=False),
        nullable=False,
        default=FundingStatus.UNKNOWN,
    )
    intake: Mapped[str | None] = mapped_column(String, nullable=True)
    application_fee: Mapped[str | None] = mapped_column(String, nullable=True)
    application_method: Mapped[str | None] = mapped_column(String, nullable=True)
    application_procedure: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_scholarship_url: Mapped[str] = mapped_column(String, nullable=False)
    official_application_url: Mapped[str | None] = mapped_column(String, nullable=True)
    lifecycle_status: Mapped[LifecycleStatus] = mapped_column(
        Enum(LifecycleStatus, name="lifecycle_status", native_enum=False),
        nullable=False,
        default=LifecycleStatus.NEWLY_DISCOVERED,
    )
    opening_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    closing_status: Mapped[str | None] = mapped_column(String, nullable=True)
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    exceptions: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="scholarship_verification_status", native_enum=False),
        nullable=False,
        default=VerificationStatus.UNVERIFIED,
    )

    requirements: Mapped[list["Requirement"]] = relationship(
        back_populates="scholarship", cascade="all, delete-orphan"
    )
    funding_details: Mapped["FundingDetails | None"] = relationship(
        back_populates="scholarship", cascade="all, delete-orphan", uselist=False
    )
    fields: Mapped[list["ScholarshipField"]] = relationship(
        back_populates="scholarship", cascade="all, delete-orphan"
    )


class ScholarshipField(Base):
    """Generic per-field value-status carrier for attributes not yet promoted
    to a first-class column (FR-INTEL-1/2/3)."""

    __tablename__ = "scholarship_fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    value_status: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scholarship: Mapped["Scholarship"] = relationship(back_populates="fields")
