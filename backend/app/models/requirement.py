import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.data.repositories.db import Base


class RequirementCategory(StrEnum):
    ELIGIBILITY = "eligibility"
    ACADEMIC = "academic"
    GPA = "gpa"
    LANGUAGE = "language"
    TEST = "test"
    AGE = "age"
    EXPERIENCE = "experience"
    NATIONALITY = "nationality"
    RESEARCH = "research"
    OTHER = "other"


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scholarship_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scholarships.id"), nullable=False, index=True
    )
    category: Mapped[RequirementCategory] = mapped_column(
        Enum(RequirementCategory, name="requirement_category", native_enum=False), nullable=False
    )
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    value_status: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, nullable=False)

    scholarship: Mapped["Scholarship"] = relationship(back_populates="requirements")  # noqa: F821
