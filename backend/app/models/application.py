"""Application Plan entities (Blueprint §14; data-model.md §8-§9).

`Application` (`id, user_id, scholarship_id, status, created_at`) shipped in
Phase 3 (Resolution note 3 in tasks.md). `Task` and `SubmissionApproval`
below are the Phase 4 additions (T118) that extend this same module.

`Task.readiness_label` uses `ReadinessLabel` (six FR-PLAN-2/SC-005 values) —
field names for both new tables are taken verbatim from data-model.md §8-§9,
not from Blueprint §14's looser prose description.

`SubmissionApproval.content_fingerprint` is a Phase 4 schema extension not
present in data-model.md §9 or Blueprint §14 — see ADR-0004. data-model.md's
`submission_scope` alone identifies *which* submission an approval covers,
but not *what content* it was approved for; a content fingerprint lets a
later re-check (constitution Principle III / FR-APP-3: "no code path may
treat an existing submission_approvals row as covering any submission other
than the one identified by its submission_scope") also detect a changed
assembled case under an unchanged scope, without consulting agent memory or
any prompt. The table stays append-only: no `updated_at`, no mutation path.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class ReadinessLabel(StrEnum):
    """Six distinct FR-PLAN-2/SC-005 outcomes — never collapsed into pairs.
    `MISSING` (absent, still obtainable by the user or the system) is not
    `USER_MUST_OBTAIN` (the system can never generate this, e.g. an official
    transcript); `NEEDS_HUMAN_REVIEW` (a draft exists, a human must read it)
    is not `NEEDS_OFFICIAL_VERIFICATION` (a fact must be confirmed against an
    official source)."""

    COMPLETE = "complete"
    MISSING = "missing"
    USER_MUST_OBTAIN = "user_must_obtain"
    AI_CAN_GENERATE = "ai_can_generate"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    NEEDS_OFFICIAL_VERIFICATION = "needs_official_verification"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="in_progress")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Task(Base):
    """The application checklist (FR-PLAN-2). `user_id` is carried directly
    (not only transitively via `application_id`) per the isolation rule that
    every user-owned table carries `user_id` from day one (constitution
    §19/FR-AUTH-2, data-model.md cross-cutting notes).

    `requirement_id` is NULLABLE and deliberately stays so: not every
    checklist item derives from a scholarship requirement (e.g. "review your
    drafted SOP" has no `requirements` row). It exists so the readiness
    service (T121) can recompute `readiness_label` deterministically via
    `Task.requirement_id -> application_documents.satisfies_requirement_id ->
    requirement_satisfaction`, rather than matching on the `description`
    string."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    readiness_label: Mapped[ReadinessLabel] = mapped_column(
        Enum(ReadinessLabel, name="task_readiness_label", native_enum=False), nullable=False
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class SubmissionApproval(Base):
    """Append-only (data-model.md §9 state transitions): an approval is never
    edited or reused, and this class deliberately defines no update path.
    No direct `user_id` column — data-model.md §9 does not list one, and
    ownership is already checked transitively via `application_id` (the
    approving user is recorded as `approved_by`, per the repository's
    ownership check before insert, T120)."""

    __tablename__ = "submission_approvals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    submission_scope: Mapped[str] = mapped_column(String, nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String, nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
