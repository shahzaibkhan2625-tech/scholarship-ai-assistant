"""Uploaded & Generated Documents (Blueprint §14; data-model.md §6, §7).

Field scope here is deliberately narrow (Slice 3A, data layer only): parsing
(T104), classification (T105), and grounded generation (T109/T110) all land
in later slices and populate `inconsistency_flags`/`source_trace` then — both
columns exist now per data-model.md §6/§7 but stay NULL on insert here.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class DocumentType(StrEnum):
    """`UNCLASSIFIED` is the only value ever set on insert — an unparsed
    upload is an honest unknown, never a guessed type (T105 classifies it
    later). The remaining members mirror data-model.md §6 / openapi.yaml's
    `DocumentType` for when classification does run."""

    UNCLASSIFIED = "unclassified"
    TRANSCRIPT = "transcript"
    DEGREE_CERTIFICATE = "degree_certificate"
    CV = "cv"
    EUROPASS_CV = "europass_cv"
    SOP = "sop"
    MOTIVATION_LETTER = "motivation_letter"
    PERSONAL_STATEMENT = "personal_statement"
    STUDY_PLAN = "study_plan"
    RESEARCH_PROPOSAL = "research_proposal"
    RECOMMENDATION_INFO = "recommendation_info"
    CERTIFICATE = "certificate"
    LANGUAGE_TEST_DOC = "language_test_doc"
    GRE_GMAT_DOC = "gre_gmat_doc"
    PORTFOLIO = "portfolio"
    PUBLICATION = "publication"
    WORK_EXPERIENCE_DOC = "work_experience_doc"
    CHARACTER_CERTIFICATE = "character_certificate"
    FINANCIAL_DOC = "financial_doc"
    SCHOLARSHIP_SPECIFIC_FORM = "scholarship_specific_form"
    UNIVERSITY_SPECIFIC_FORM = "university_specific_form"
    OTHER = "other"


class GeneratedDocumentType(StrEnum):
    CV = "cv"
    SOP = "sop"
    MOTIVATION = "motivation"
    OTHER = "other"


class ApplicationDocument(Base):
    """A user-uploaded document. `file_ref` is a relative storage key, never
    an absolute filesystem path (the storage backend that resolves it is
    T103, a later slice)."""

    __tablename__ = "application_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="application_document_type", native_enum=False),
        nullable=False,
        default=DocumentType.UNCLASSIFIED,
    )
    file_ref: Mapped[str] = mapped_column(String, nullable=False)
    parsed_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    satisfies_requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id"), nullable=True
    )
    inconsistency_flags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeneratedDocument(Base):
    """An AI-generated CV/SOP/motivation draft. `source_trace` (claim →
    grounding, `[{claim, profile_field_or_document_id}]`) is populated by the
    `ground_check`-gated generation workflows (T109/T110) — it stays NULL
    here and is required to cover 100% of factual claims before those later
    workflows write a row (FR-GEN-2/SC-004)."""

    __tablename__ = "generated_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False, index=True
    )
    type: Mapped[GeneratedDocumentType] = mapped_column(
        Enum(GeneratedDocumentType, name="generated_document_type", native_enum=False), nullable=False
    )
    file_ref: Mapped[str] = mapped_column(String, nullable=False)
    source_trace: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
