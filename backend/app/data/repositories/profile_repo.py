import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.profile import EducationRecord, Experience, MissingInfo, Profile, ProfileCriterion, TestScore


def get_by_user_id(db: Session, user_id: uuid.UUID) -> Profile | None:
    stmt = (
        select(Profile)
        .where(Profile.user_id == user_id)
        .options(
            selectinload(Profile.education_records),
            selectinload(Profile.test_scores),
            selectinload(Profile.experience),
            selectinload(Profile.criteria),
            selectinload(Profile.missing_info),
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def create_for_user(db: Session, user_id: uuid.UUID) -> Profile:
    profile = Profile(user_id=user_id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def get_or_create_for_user(db: Session, user_id: uuid.UUID) -> Profile:
    profile = get_by_user_id(db, user_id)
    if profile is not None:
        return profile
    return create_for_user(db, user_id)


def update_fields(db: Session, profile: Profile, updates: dict) -> Profile:
    for field, value in updates.items():
        if value is not None:
            setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


def upsert_criterion(
    db: Session,
    profile: Profile,
    *,
    criterion_id: uuid.UUID | None,
    dimension: str,
    operator: str,
    value: object,
    kind: str,
    weight: float | None,
    note: str | None,
) -> ProfileCriterion:
    """Reclassifying a criterion (changing `kind`) is an update to the same
    row, never a new row, so matching always reads the current classification
    (data-model.md `profile_criteria` validation rule)."""
    criterion: ProfileCriterion | None = None
    if criterion_id is not None:
        criterion = next((c for c in profile.criteria if c.id == criterion_id), None)

    if criterion is None:
        criterion = ProfileCriterion(profile_id=profile.id, dimension=dimension, operator=operator, value=value, kind=kind, weight=weight, note=note)
        db.add(criterion)
    else:
        criterion.dimension = dimension
        criterion.operator = operator
        criterion.value = value
        criterion.kind = kind
        criterion.weight = weight
        criterion.note = note

    db.commit()
    db.refresh(criterion)
    return criterion


def replace_education_records(db: Session, profile: Profile, rows: list[dict]) -> None:
    for existing in list(profile.education_records):
        db.delete(existing)
    db.flush()
    db.add_all(EducationRecord(profile_id=profile.id, **row) for row in rows)
    db.commit()
    db.refresh(profile)


def replace_test_scores(db: Session, profile: Profile, rows: list[dict]) -> None:
    for existing in list(profile.test_scores):
        db.delete(existing)
    db.flush()
    db.add_all(TestScore(profile_id=profile.id, **row) for row in rows)
    db.commit()
    db.refresh(profile)


def replace_experience(db: Session, profile: Profile, rows: list[dict]) -> None:
    for existing in list(profile.experience):
        db.delete(existing)
    db.flush()
    db.add_all(Experience(profile_id=profile.id, **row) for row in rows)
    db.commit()
    db.refresh(profile)


def replace_missing_info(db: Session, profile: Profile, items: list[dict]) -> list[MissingInfo]:
    """Recomputes the full missing-info list for a profile (never silently
    omitted — FR-PROFILE-3)."""
    for existing in list(profile.missing_info):
        db.delete(existing)
    db.flush()

    new_rows = [MissingInfo(profile_id=profile.id, dimension=item["dimension"], reason=item["reason"]) for item in items]
    db.add_all(new_rows)
    db.commit()
    db.refresh(profile)
    return profile.missing_info
