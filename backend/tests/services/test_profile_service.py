"""Unit tests: missing_info tracking + profile_criteria.kind validation
(US1 Acceptance Scenario 3; data-model.md profile_criteria validation rule).
Pure unit tests — no DB required, transient ORM objects only."""

import pytest

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
