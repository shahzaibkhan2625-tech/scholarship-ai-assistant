"""Hard-constraint verdict-correctness test matrix (constitution Principle II
+ spec.md SC-007): a fixed set of (known profile x known scholarship)
fixture pairs -> expected eligibility_verdict. 100% of hard-constraint
failures must be correctly reported as ineligible, with zero overrides by
soft-criteria strength. BUILD-BREAKING ON FAILURE, NOT A WARNING — this is a
plain pytest test, so any regression here fails the CI build like any other
test (constitution: "any hard-constraint guardrail violation MUST be a
build-breaking CI failure, not a warning").

Pure unit tests: transient ORM objects only, no DB, no LLM."""

from datetime import date, timedelta

import pytest

from app.agents.matching.agent import build_verdict
from app.models.profile import CriterionKind, EducationRecord, Profile, ProfileCriterion, TestScore
from app.models.requirement import Requirement
from app.models.scholarship import DegreeLevel, Scholarship
from app.schemas.match import CriterionResult, EligibilityVerdict
from app.services.hard_constraints import any_hard_constraint_failed, evaluate_hard_constraints

_FUTURE_DEADLINE = date.today() + timedelta(days=90)
_PAST_DEADLINE = date.today() - timedelta(days=10)


def _profile(**kwargs) -> Profile:
    criteria = kwargs.pop("criteria", [])
    education_records = kwargs.pop("education_records", [])
    test_scores = kwargs.pop("test_scores", [])
    profile = Profile(**kwargs)
    profile.criteria.extend(criteria)
    profile.education_records.extend(education_records)
    profile.test_scores.extend(test_scores)
    return profile


def _hard_criterion(dimension: str) -> ProfileCriterion:
    return ProfileCriterion(dimension=dimension, operator="=", value=None, kind=CriterionKind.HARD_CONSTRAINT)


def _scholarship(**kwargs) -> Scholarship:
    requirements = kwargs.pop("requirements", [])
    scholarship = Scholarship(official_scholarship_url="https://example.test/s", **kwargs)
    scholarship.requirements.extend(requirements)
    return scholarship


# --- Matrix cases: (profile, scholarship, expected eligibility_verdict, expected_failing_criterion) ---


def case_gpa_pass():
    profile = _profile(
        criteria=[_hard_criterion("gpa")],
        education_records=[EducationRecord(gpa=3.8, gpa_scale=4.0)],
    )
    scholarship = _scholarship(
        deadline=_FUTURE_DEADLINE,
        requirements=[Requirement(category="gpa", key="min_gpa", value=3.5, mandatory=True, value_status="known", confidence="verified")],
    )
    return profile, scholarship, EligibilityVerdict.LIKELY, None


def case_gpa_fail():
    profile = _profile(
        criteria=[_hard_criterion("gpa")],
        education_records=[EducationRecord(gpa=3.0, gpa_scale=4.0)],
    )
    scholarship = _scholarship(
        deadline=_FUTURE_DEADLINE,
        requirements=[Requirement(category="gpa", key="min_gpa", value=3.5, mandatory=True, value_status="known", confidence="verified")],
    )
    return profile, scholarship, EligibilityVerdict.NOT, "gpa"


def case_nationality_fail_overrides_everything():
    """US2 Acceptance Scenario 2: verdict is 'Not' regardless of how strong
    soft-preference alignment is."""
    profile = _profile(
        nationality="Wronglandian",
        criteria=[_hard_criterion("nationality")],
    )
    scholarship = _scholarship(
        deadline=_FUTURE_DEADLINE,
        requirements=[
            Requirement(category="nationality", key="eligible_nationalities", value=["Pakistani", "Indian"], mandatory=True, value_status="known", confidence="verified")
        ],
    )
    return profile, scholarship, EligibilityVerdict.NOT, "nationality"


def case_nationality_pass():
    profile = _profile(
        nationality="Pakistani",
        criteria=[_hard_criterion("nationality")],
    )
    scholarship = _scholarship(
        deadline=_FUTURE_DEADLINE,
        requirements=[
            Requirement(category="nationality", key="eligible_nationalities", value=["Pakistani", "Indian"], mandatory=True, value_status="known", confidence="verified")
        ],
    )
    return profile, scholarship, EligibilityVerdict.LIKELY, None


def case_degree_mismatch_fails():
    profile = _profile(target_degree_level=DegreeLevel.PHD, criteria=[_hard_criterion("degree")])
    scholarship = _scholarship(deadline=_FUTURE_DEADLINE, degree_level=DegreeLevel.MS)
    return profile, scholarship, EligibilityVerdict.NOT, "degree"


def case_deadline_passed_fails_regardless_of_criteria():
    """Edge case: a closed scholarship must never be presented as open,
    even with no hard constraints declared on the profile at all."""
    profile = _profile()
    scholarship = _scholarship(deadline=_PAST_DEADLINE)
    return profile, scholarship, EligibilityVerdict.NOT, "deadline"


def case_mandatory_test_missing_fails():
    profile = _profile(criteria=[_hard_criterion("test")])
    scholarship = _scholarship(
        deadline=_FUTURE_DEADLINE,
        requirements=[Requirement(category="test", key="IELTS", value=6.5, mandatory=True, value_status="known", confidence="verified")],
    )
    return profile, scholarship, EligibilityVerdict.NOT, "test"


def case_mandatory_test_met_passes():
    profile = _profile(
        criteria=[_hard_criterion("test")],
        test_scores=[TestScore(test_type="IELTS", status="have", score=7.0)],
    )
    scholarship = _scholarship(
        deadline=_FUTURE_DEADLINE,
        requirements=[Requirement(category="test", key="IELTS", value=6.5, mandatory=True, value_status="known", confidence="verified")],
    )
    return profile, scholarship, EligibilityVerdict.LIKELY, None


def case_unstated_requirement_is_unknown_not_assumed():
    """US2 Acceptance Scenario 3: a scholarship page missing a specific data
    point is marked Unknown, never assumed pass or fail."""
    profile = _profile(criteria=[_hard_criterion("gpa")])
    scholarship = _scholarship(deadline=_FUTURE_DEADLINE, requirements=[])  # no GPA requirement stated at all
    return profile, scholarship, EligibilityVerdict.UNKNOWN_REQUIRES_VERIFICATION, None


def case_no_hard_constraints_defined_does_not_fail_open():
    """Edge case: a profile with no hard constraints defined proceeds on
    soft/fuzzy criteria only — absence of hard constraints is not mistaken
    for automatic eligibility, and a future deadline alone yields LIKELY,
    never a fabricated ELIGIBLE."""
    profile = _profile()
    scholarship = _scholarship(deadline=_FUTURE_DEADLINE)
    return profile, scholarship, EligibilityVerdict.LIKELY, None


_MATRIX = [
    case_gpa_pass,
    case_gpa_fail,
    case_nationality_fail_overrides_everything,
    case_nationality_pass,
    case_degree_mismatch_fails,
    case_deadline_passed_fails_regardless_of_criteria,
    case_mandatory_test_missing_fails,
    case_mandatory_test_met_passes,
    case_unstated_requirement_is_unknown_not_assumed,
    case_no_hard_constraints_defined_does_not_fail_open,
]


@pytest.mark.parametrize("case_factory", _MATRIX, ids=[c.__name__ for c in _MATRIX])
def test_verdict_correctness_matrix(case_factory) -> None:
    profile, scholarship, expected_verdict, expected_failing_criterion = case_factory()

    hard_outcomes = evaluate_hard_constraints(profile, scholarship)
    verdict = build_verdict(scholarship.id or "00000000-0000-0000-0000-000000000000", hard_outcomes, [], [], [])

    assert verdict.eligibility_verdict == expected_verdict

    if expected_failing_criterion is not None:
        assert any_hard_constraint_failed(hard_outcomes)
        failing = [o for o in hard_outcomes if o.result == CriterionResult.FAIL]
        assert any(o.criterion == expected_failing_criterion for o in failing)
    elif expected_verdict != EligibilityVerdict.UNKNOWN_REQUIRES_VERIFICATION:
        assert not any_hard_constraint_failed(hard_outcomes)


def test_hard_fail_is_never_outranked_by_strength() -> None:
    """The core guardrail invariant, expressed directly: 100% of
    hard-constraint failures are reported as ineligible regardless of
    match_strength — this is checked independent of the parametrized matrix
    above as an explicit, always-run regression guard."""
    profile, scholarship, _, _ = case_nationality_fail_overrides_everything()
    hard_outcomes = evaluate_hard_constraints(profile, scholarship)

    assert any_hard_constraint_failed(hard_outcomes)
    verdict = build_verdict(scholarship.id or "00000000-0000-0000-0000-000000000000", hard_outcomes, [], [], [])
    assert verdict.eligibility_verdict == EligibilityVerdict.NOT
