"""Unit tests: missing_info tracking + profile_criteria.kind validation
(US1 Acceptance Scenario 3; data-model.md profile_criteria validation rule).
Pure unit tests — no DB required, transient ORM objects only."""

import uuid

import pytest

from app.data.repositories import profile_repo
from app.models.profile import DegreeLevel, EducationRecord, Profile
from app.services.profile import InvalidCriterionKindError, compute_missing_info, upsert_criterion


def test_missing_info_flags_every_unanswered_dimension() -> None:
    profile = Profile()  # transient, nothing set

    missing = compute_missing_info(profile)
    dimensions = {item["dimension"] for item in missing}

    assert "nationality" in dimensions
    assert "country_of_residence" in dimensions
    assert "target_degree_level" in dimensions
    assert "target_fields" in dimensions
    assert "gpa" in dimensions
    assert "test_scores" in dimensions
    for item in missing:
        assert item["reason"]


def test_missing_info_omits_dimensions_that_are_answered() -> None:
    profile = Profile(
        nationality="Pakistani",
        country_of_residence="Pakistan",
        target_degree_level=DegreeLevel.MS,
        target_fields=["Computer Science"],
    )
    profile.education_records.append(EducationRecord(gpa=3.8, gpa_scale=4.0))

    missing = compute_missing_info(profile)
    dimensions = {item["dimension"] for item in missing}

    assert "nationality" not in dimensions
    assert "country_of_residence" not in dimensions
    assert "target_degree_level" not in dimensions
    assert "target_fields" not in dimensions
    assert "gpa" not in dimensions
    # test_scores still unanswered
    assert "test_scores" in dimensions


def test_missing_info_never_silently_omits_a_partially_filled_profile() -> None:
    """US1 Scenario 3: an unanswered field is reported, not dropped, even
    when most of the profile is complete."""
    profile = Profile(
        nationality="Pakistani",
        country_of_residence="Pakistan",
        target_degree_level=DegreeLevel.MS,
        target_fields=["Computer Science"],
    )
    profile.education_records.append(EducationRecord(gpa=3.8, gpa_scale=4.0))
    # test_scores intentionally left empty

    missing = compute_missing_info(profile)

    assert len(missing) == 1
    assert missing[0]["dimension"] == "test_scores"


@pytest.mark.parametrize("kind", ["not_a_real_kind", "", "HARD_CONSTRAINT"])
def test_upsert_criterion_rejects_invalid_kind(kind: str) -> None:
    with pytest.raises(InvalidCriterionKindError):
        upsert_criterion(
            None,  # db is never touched — validation happens before any DB access
            None,  # user_id likewise unused on the rejected path
            criterion_id=None,
            dimension="nationality",
            operator="=",
            value="Pakistani",
            kind=kind,
            weight=None,
            note=None,
        )


@pytest.mark.parametrize("kind", ["hard_constraint", "soft_preference", "exclusion"])
def test_valid_criterion_kinds_are_accepted_values(kind: str) -> None:
    from app.services.profile import _VALID_KINDS

    assert kind in _VALID_KINDS


# T132 hardening: profile_repo's six object-based mutators must reject a
# caller-supplied user_id that does not match the loaded Profile's owner,
# mirroring document_repo's `if document.user_id != user_id: raise
# PermissionError(...)` guard. The check is the first line of each function
# (before any DB access), so — same as test_upsert_criterion_rejects_invalid_kind
# above — db is never touched on the rejected path and a transient Profile
# suffices; no fixture/session needed.


def _foreign_user_id(profile: Profile) -> uuid.UUID:
    other = uuid.uuid4()
    while other == profile.user_id:
        other = uuid.uuid4()
    return other


def test_update_fields_rejects_mismatched_user_id() -> None:
    profile = Profile(user_id=uuid.uuid4())
    with pytest.raises(PermissionError):
        profile_repo.update_fields(None, _foreign_user_id(profile), profile, {"nationality": "Pakistani"})


def test_upsert_criterion_repo_rejects_mismatched_user_id() -> None:
    profile = Profile(user_id=uuid.uuid4())
    with pytest.raises(PermissionError):
        profile_repo.upsert_criterion(
            None,
            _foreign_user_id(profile),
            profile,
            criterion_id=None,
            dimension="nationality",
            operator="=",
            value="Pakistani",
            kind="hard_constraint",
            weight=None,
            note=None,
        )


def test_replace_education_records_rejects_mismatched_user_id() -> None:
    profile = Profile(user_id=uuid.uuid4())
    with pytest.raises(PermissionError):
        profile_repo.replace_education_records(None, _foreign_user_id(profile), profile, [])


def test_replace_test_scores_rejects_mismatched_user_id() -> None:
    profile = Profile(user_id=uuid.uuid4())
    with pytest.raises(PermissionError):
        profile_repo.replace_test_scores(None, _foreign_user_id(profile), profile, [])


def test_replace_experience_rejects_mismatched_user_id() -> None:
    profile = Profile(user_id=uuid.uuid4())
    with pytest.raises(PermissionError):
        profile_repo.replace_experience(None, _foreign_user_id(profile), profile, [])


def test_replace_missing_info_rejects_mismatched_user_id() -> None:
    profile = Profile(user_id=uuid.uuid4())
    with pytest.raises(PermissionError):
        profile_repo.replace_missing_info(None, _foreign_user_id(profile), profile, [])
