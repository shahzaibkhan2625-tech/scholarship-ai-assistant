"""Source Registry & governance (Blueprint §14, §29; data-model.md §3).

These three tables are system-owned, not user-owned — no `user_id` column.
Sources are curated/approved by operators, not scoped per end-user, so the
per-user isolation discipline used elsewhere (`user_id` FK + WHERE-clause
scoping) does not apply here. Governance is enforced by table separation
(only `source_registry` rows are ever treated as authoritative) plus a
`status == active` filter in the repository layer — not by prompt
instruction to any agent (constitution Principle IV).

`SourceStatus` adds `FAILING` alongside the `active/pending/disabled` states
in data-model.md so a source with a degrading fetch history (tracked via
`source_fetch_log`, §33) can be reflected before an operator disables it
outright; `get_active_sources`/`get_source_by_id` still gate on `ACTIVE`
only, so `failing` is excluded exactly like `disabled`.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base
from app.models.scholarship import VerificationStatus


class SourceType(StrEnum):
    GOV = "gov"
    NATIONAL_EDUCATION = "national_education"
    UNIVERSITY = "university"
    DEPARTMENT = "department"
    PROVIDER = "provider"
    FOUNDATION = "foundation"
    NGO = "ngo"
    EMBASSY = "embassy"
    INTERNATIONAL_ORG = "international_org"
    RESEARCH = "research"
    API = "api"
    APPROVED_AGGREGATOR = "approved_aggregator"


class AccessMethod(StrEnum):
    API = "api"
    MCP = "mcp"
    WEB = "web"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"
    DISABLED = "disabled"
    FAILING = "failing"


class ReliabilityLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CandidateSourceStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FetchStatus(StrEnum):
    OK = "ok"
    FAIL = "fail"
    TIMEOUT = "timeout"


class SourceRegistry(Base):
    """Approved, authoritative sources only — the sole table connectors may
    fetch from (never `candidate_sources`, constitution Principle IV)."""

    __tablename__ = "source_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    organization: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[SourceType] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=False), nullable=False
    )
    official_status: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    access_method: Mapped[AccessMethod] = mapped_column(
        Enum(AccessMethod, name="access_method", native_enum=False), nullable=False
    )
    discovery_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_role: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reliability_level: Mapped[ReliabilityLevel] = mapped_column(
        Enum(ReliabilityLevel, name="reliability_level", native_enum=False), nullable=False
    )
    update_frequency: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[SourceStatus] = mapped_column(
        Enum(SourceStatus, name="source_status", native_enum=False),
        nullable=False,
        default=SourceStatus.ACTIVE,
    )
    extraction_rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CandidateSource(Base):
    """Controlled expansion (§29) — never joined as authoritative until
    promoted (`status == approved`) into `source_registry`."""

    __tablename__ = "candidate_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    discovered_from: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    proposed_type: Mapped[SourceType | None] = mapped_column(
        Enum(SourceType, name="source_type", native_enum=False), nullable=True
    )
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[CandidateSourceStatus] = mapped_column(
        Enum(CandidateSourceStatus, name="candidate_source_status", native_enum=False),
        nullable=False,
        default=CandidateSourceStatus.PENDING,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourceFetchLog(Base):
    """Coverage + health + retry (§33) — a failed fetch is a recorded gap,
    never a silent drop."""

    __tablename__ = "source_fetch_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_registry.id"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[FetchStatus] = mapped_column(
        Enum(FetchStatus, name="fetch_status", native_enum=False), nullable=False
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_found: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ScholarshipSource(Base):
    """Per-scholarship provenance (§14) — links a scholarship to the
    registry source it was retrieved/verified from."""

    __tablename__ = "scholarship_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_registry.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="scholarship_source_verification_status", native_enum=False),
        nullable=False,
        default=VerificationStatus.UNVERIFIED,
    )
    reliability: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
