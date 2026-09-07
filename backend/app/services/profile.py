"""Profile service — CRUD, criteria classify/reclassify, missing-info
computation. Deterministic; no LLM involvement (profile management is a
service per plan.md's Architecture Decision Matrix, not an agent)."""

import uuid

from sqlalchemy.orm import Session

from app.data.repositories import profile_repo
from app.models.profile import CriterionKind, Profile, ProfileCriterion

_VALID_KINDS = {kind.value for kind in CriterionKind}

# Fixed set of profile dimensions tracked for missing-info (US1 Acceptance
# Scenario 3: unanswered fields are surfaced, never silently omitted).
_TRACKED_DIMENSIONS = (
    "nationality",
    "country_of_residence",
    "target_degree_level",
    "target_fields",
    "gpa",
    "test_scores",
)


class InvalidCriterionKindError(ValueError):
    pass


def get_or_create_profile(db: Session, user_id: uuid.UUID) -> Profile:
    return profile_repo.get_or_create_for_user(db, user_id)


def update_profile(
    db: Session,
    user_id: uuid.UUID,
    updates: dict,
    *,
    education_records: list[dict] | None = None,
    test_scores: list[dict] | None = None,
    experience: list[dict] | None = None,
) -> Profile:
    profile = profile_repo.get_or_create_for_user(db, user_id)
    profile = profile_repo.update_fields(db, profile, updates)

    if education_records is not None:
        profile_repo.replace_education_records(db, profile, education_records)
    if test_scores is not None:
        profile_repo.replace_test_scores(db, profile, test_scores)
    if experience is not None:
        profile_repo.replace_experience(db, profile, experience)

    recompute_missing_info(db, profile)
    return profile


def upsert_criterion(
    db: Session,
    user_id: uuid.UUID,
    *,
    criterion_id: uuid.UUID | None,
    dimension: str,
    operator: str,
    value: object,
    kind: str,
    weight: float | None,
    note: str | None,
) -> list[ProfileCriterion]:
    if kind not in _VALID_KINDS:
        raise InvalidCriterionKindError(
            f"kind must be one of {sorted(_VALID_KINDS)}, got {kind!r}"
        )

    profile = profile_repo.get_or_create_for_user(db, user_id)
    profile_repo.upsert_criterion(
        db,
        profile,
        criterion_id=criterion_id,
        dimension=dimension,
        operator=operator,
        value=value,
        kind=kind,
        weight=weight,
        note=note,
    )
    db.refresh(profile)
    return profile.criteria


def compute_missing_info(profile: Profile) -> list[dict]:
    missing: list[dict] = []
    if not profile.nationality:
        missing.append({"dimension": "nationality", "reason": "Nationality not provided"})
    if not profile.country_of_residence:
        missing.append({"dimension": "country_of_residence", "reason": "Country of residence not provided"})
    if not profile.target_degree_level:
        missing.append({"dimension": "target_degree_level", "reason": "Target degree level not provided"})
    if not profile.target_fields:
        missing.append({"dimension": "target_fields", "reason": "Target field(s) of study not provided"})
    if not any(record.gpa is not None for record in profile.education_records):
        missing.append({"dimension": "gpa", "reason": "No education record with a GPA provided"})
    if not profile.test_scores:
        missing.append({"dimension": "test_scores", "reason": "No standardized test score information provided"})
    return missing


def recompute_missing_info(db: Session, profile: Profile) -> list:
    return profile_repo.replace_missing_info(db, profile, compute_missing_info(profile))
