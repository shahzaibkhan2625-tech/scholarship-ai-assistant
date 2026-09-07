import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.data.repositories.db import Base


class DegreeLevel(StrEnum):
    BS = "BS"
    MS = "MS"
    PHD = "PhD"
    OTHER = "other"


class TestType(StrEnum):
    IELTS = "IELTS"
    TOEFL = "TOEFL"
    PTE = "PTE"
    GRE = "GRE"
    GMAT = "GMAT"
    OTHER = "other"


class TestStatus(StrEnum):
    HAVE = "have"
    PLANNED = "planned"
    NONE = "none"


class ExperienceKind(StrEnum):
    WORK = "work"
    RESEARCH = "research"
    PUBLICATION = "publication"
    PROJECT = "project"


class CriterionDimension(StrEnum):
    DEGREE = "degree"
    FUNDING = "funding"
    FIELD = "field"
    COUNTRY = "country"
    NATIONALITY = "nationality"
    LANGUAGE = "language"
    TEST = "test"
    GPA = "gpa"
    SPOUSE_DEPENDENT = "spouse_dependent"
    OTHER = "other"


class CriterionKind(StrEnum):
    HARD_CONSTRAINT = "hard_constraint"
    SOFT_PREFERENCE = "soft_preference"
    EXCLUSION = "exclusion"


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String, nullable=True)
    country_of_residence: Mapped[str | None] = mapped_column(String, nullable=True)
    current_degree: Mapped[str | None] = mapped_column(String, nullable=True)
    target_degree_level: Mapped[DegreeLevel | None] = mapped_column(
        Enum(DegreeLevel, name="degree_level", native_enum=False), nullable=True
    )
    target_fields: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    target_countries: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    education_records: Mapped[list["EducationRecord"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    test_scores: Mapped[list["TestScore"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    experience: Mapped[list["Experience"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    criteria: Mapped[list["ProfileCriterion"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    missing_info: Mapped[list["MissingInfo"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class EducationRecord(Base):
    __tablename__ = "education_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False, index=True
    )
    degree: Mapped[str | None] = mapped_column(String, nullable=True)
    field: Mapped[str | None] = mapped_column(String, nullable=True)
    university: Mapped[str | None] = mapped_column(String, nullable=True)
    gpa: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    gpa_scale: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    start: Mapped[date | None] = mapped_column(Date, nullable=True)
    end: Mapped[date | None] = mapped_column(Date, nullable=True)

    profile: Mapped["Profile"] = relationship(back_populates="education_records")


class TestScore(Base):
    __tablename__ = "test_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False, index=True
    )
    test_type: Mapped[TestType] = mapped_column(Enum(TestType, name="test_type", native_enum=False), nullable=False)
    status: Mapped[TestStatus] = mapped_column(
        Enum(TestStatus, name="test_status", native_enum=False), nullable=False
    )
    score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    taken_at: Mapped[date | None] = mapped_column(Date, nullable=True)

    profile: Mapped["Profile"] = relationship(back_populates="test_scores")


class Experience(Base):
    __tablename__ = "experience"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False, index=True
    )
    kind: Mapped[ExperienceKind] = mapped_column(
        Enum(ExperienceKind, name="experience_kind", native_enum=False), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    org: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    start: Mapped[date | None] = mapped_column(Date, nullable=True)
    end: Mapped[date | None] = mapped_column(Date, nullable=True)

    profile: Mapped["Profile"] = relationship(back_populates="experience")


class ProfileCriterion(Base):
    __tablename__ = "profile_criteria"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False, index=True
    )
    dimension: Mapped[CriterionDimension] = mapped_column(
        Enum(CriterionDimension, name="criterion_dimension", native_enum=False), nullable=False
    )
    operator: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[object] = mapped_column(JSONB, nullable=False)
    kind: Mapped[CriterionKind] = mapped_column(Enum(CriterionKind, name="criterion_kind", native_enum=False), nullable=False)
    weight: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped["Profile"] = relationship(back_populates="criteria")


class MissingInfo(Base):
    __tablename__ = "missing_info"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False, index=True
    )
    dimension: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    profile: Mapped["Profile"] = relationship(back_populates="missing_info")
