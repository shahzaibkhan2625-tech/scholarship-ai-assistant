"""Profile request/response schemas — mirrors contracts/openapi.yaml's
Profile / ProfileUpdateRequest / ProfileCriterion(Request) / MissingInfoItem."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.profile import CriterionDimension, CriterionKind, DegreeLevel, ExperienceKind, TestStatus, TestType


class EducationRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    degree: str | None = None
    field: str | None = None
    university: str | None = None
    gpa: float | None = None
    gpa_scale: float | None = None
    start: date | None = None
    end: date | None = None


class TestScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    test_type: TestType
    status: TestStatus
    score: float | None = None
    taken_at: date | None = None


class ExperienceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ExperienceKind
    title: str | None = None
    org: str | None = None
    detail: str | None = None
    start: date | None = None
    end: date | None = None


class ProfileCriterion(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dimension: CriterionDimension
    operator: str
    value: object = None
    kind: CriterionKind
    weight: float | None = None
    note: str | None = None


class ProfileCriterionRequest(BaseModel):
    id: uuid.UUID | None = None
    dimension: CriterionDimension
    operator: str
    value: object = None
    kind: CriterionKind
    weight: float | None = None
    note: str | None = None


class MissingInfoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dimension: str
    reason: str


class Profile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None = None
    nationality: str | None = None
    country_of_residence: str | None = None
    current_degree: str | None = None
    target_degree_level: DegreeLevel | None = None
    target_fields: list[str] = []
    target_countries: list[str] = []
    education_records: list[EducationRecordOut] = []
    test_scores: list[TestScoreOut] = []
    experience: list[ExperienceOut] = []
    criteria: list[ProfileCriterion] = []
    missing_info: list[MissingInfoItem] = []
    updated_at: datetime


class EducationRecordIn(BaseModel):
    degree: str | None = None
    field: str | None = None
    university: str | None = None
    gpa: float | None = None
    gpa_scale: float | None = None
    start: date | None = None
    end: date | None = None


class TestScoreIn(BaseModel):
    test_type: TestType
    status: TestStatus
    score: float | None = None
    taken_at: date | None = None


class ExperienceIn(BaseModel):
    kind: ExperienceKind
    title: str | None = None
    org: str | None = None
    detail: str | None = None
    start: date | None = None
    end: date | None = None


class ProfileUpdateRequest(BaseModel):
    """Partial update — any subset of Profile's writable fields (living
    profile, not a one-time form — FR-PROFILE-1). Scalar fields are true
    partial updates (omitted = unchanged). `education_records`/`test_scores`/
    `experience`, when present, replace that satellite table's rows wholesale
    for this profile — the contract's own note that education/test/experience
    are "any subset of Profile's writable fields" extends to these nested
    lists since data-model.md defines no per-row update endpoint for them."""

    name: str | None = None
    nationality: str | None = None
    country_of_residence: str | None = None
    current_degree: str | None = None
    target_degree_level: DegreeLevel | None = None
    target_fields: list[str] | None = None
    target_countries: list[str] | None = None
    education_records: list[EducationRecordIn] | None = None
    test_scores: list[TestScoreIn] | None = None
    experience: list[ExperienceIn] | None = None

    def to_update_dict(self) -> dict:
        return self.model_dump(exclude_unset=True, exclude={"education_records", "test_scores", "experience"})
