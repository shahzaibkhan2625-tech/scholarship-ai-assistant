"""`hard_constraints` service — deterministic GPA/degree/nationality/deadline/
mandatory-test evaluation. NO LLM INVOLVEMENT ANYWHERE IN THIS MODULE
(constitution Principle II, NON-NEGOTIABLE): every function here is a pure,
auditable rule evaluated against structured profile/scholarship data. This
module's output is passed **read-only** into the Matching agent — nothing
downstream may alter, override, or re-derive a result computed here."""

from datetime import date, datetime, timezone

from app.models.profile import CriterionKind, ProfileCriterion
from app.models.requirement import Requirement
from app.models.scholarship import Scholarship
from app.schemas.match import CriterionOutcome, CriterionResult

# Maps a profile_criteria.dimension to the requirement category(ies) on the
# scholarship side that can substantiate it. "other" dimensions can't be
# automatically resolved and always surface as unknown (never guessed).
_DIMENSION_TO_REQUIREMENT_CATEGORY = {
    "gpa": "gpa",
    "nationality": "nationality",
    "degree": "academic",
    "test": "test",
    "language": "language",
}


def _find_requirements(scholarship: Scholarship, category: str) -> list[Requirement]:
    return [r for r in scholarship.requirements if r.category == category]


def _evaluate_deadline(scholarship: Scholarship, *, today: date | None = None) -> CriterionOutcome:
    """Universal check — not driven by any profile_criteria row. A passed
    deadline is always a hard fail, regardless of what the student did or
    did not mark as a hard constraint (Edge Cases: a closed scholarship must
    never be presented as open)."""
    today = today or datetime.now(timezone.utc).date()
    if scholarship.deadline is None:
        return CriterionOutcome(criterion="deadline", result=CriterionResult.UNKNOWN)

    if scholarship.deadline < today:
        return CriterionOutcome(
            criterion="deadline",
            result=CriterionResult.FAIL,
            evidence=f"Deadline {scholarship.deadline.isoformat()} has passed (checked {today.isoformat()}).",
        )
    return CriterionOutcome(
        criterion="deadline",
        result=CriterionResult.PASS,
        evidence=f"Deadline {scholarship.deadline.isoformat()} has not yet passed (checked {today.isoformat()}).",
    )


def _evaluate_gpa(criterion: ProfileCriterion, scholarship: Scholarship, student_gpas: list[float]) -> CriterionOutcome:
    requirements = _find_requirements(scholarship, "gpa")
    if not requirements or requirements[0].value_status != "known" or requirements[0].value is None:
        return CriterionOutcome(criterion="gpa", result=CriterionResult.UNKNOWN)

    required_gpa = float(requirements[0].value)
    if not student_gpas:
        return CriterionOutcome(criterion="gpa", result=CriterionResult.UNKNOWN)

    best_gpa = max(student_gpas)
    if best_gpa >= required_gpa:
        return CriterionOutcome(
            criterion="gpa",
            result=CriterionResult.PASS,
            evidence=f"Required GPA >= {required_gpa}; student's best recorded GPA is {best_gpa}.",
        )
    return CriterionOutcome(
        criterion="gpa",
        result=CriterionResult.FAIL,
        evidence=f"Required GPA >= {required_gpa}; student's best recorded GPA is {best_gpa}.",
    )


def _evaluate_nationality(criterion: ProfileCriterion, scholarship: Scholarship, student_nationality: str | None) -> CriterionOutcome:
    requirements = _find_requirements(scholarship, "nationality")
    if not requirements or requirements[0].value_status != "known" or requirements[0].value is None:
        return CriterionOutcome(criterion="nationality", result=CriterionResult.UNKNOWN)

    accepted = requirements[0].value
    accepted_set = {str(n).strip().lower() for n in accepted} if isinstance(accepted, list) else {str(accepted).strip().lower()}

    if not student_nationality:
        return CriterionOutcome(criterion="nationality", result=CriterionResult.UNKNOWN)

    if student_nationality.strip().lower() in accepted_set:
        return CriterionOutcome(
            criterion="nationality",
            result=CriterionResult.PASS,
            evidence=f"Scholarship accepts nationalities {sorted(accepted_set)}; student is {student_nationality}.",
        )
    return CriterionOutcome(
        criterion="nationality",
        result=CriterionResult.FAIL,
        evidence=f"Scholarship accepts nationalities {sorted(accepted_set)}; student is {student_nationality}.",
    )


def _evaluate_degree(criterion: ProfileCriterion, scholarship: Scholarship, student_target_degree: str | None) -> CriterionOutcome:
    if scholarship.degree_level is None:
        return CriterionOutcome(criterion="degree", result=CriterionResult.UNKNOWN)
    if student_target_degree is None:
        return CriterionOutcome(criterion="degree", result=CriterionResult.UNKNOWN)

    scholarship_level = str(scholarship.degree_level.value if hasattr(scholarship.degree_level, "value") else scholarship.degree_level)
    if scholarship_level == student_target_degree:
        return CriterionOutcome(
            criterion="degree",
            result=CriterionResult.PASS,
            evidence=f"Scholarship is for {scholarship_level}; student's target degree level is {student_target_degree}.",
        )
    return CriterionOutcome(
        criterion="degree",
        result=CriterionResult.FAIL,
        evidence=f"Scholarship is for {scholarship_level}; student's target degree level is {student_target_degree}.",
    )


def _evaluate_test(criterion: ProfileCriterion, scholarship: Scholarship, student_test_scores: list) -> CriterionOutcome:
    requirements = _find_requirements(scholarship, "test")
    mandatory_requirements = [r for r in requirements if r.mandatory]
    if not mandatory_requirements or mandatory_requirements[0].value_status != "known":
        return CriterionOutcome(criterion="test", result=CriterionResult.UNKNOWN)

    requirement = mandatory_requirements[0]
    required_test_type = str(requirement.key).upper()
    matching = [s for s in student_test_scores if str(s.test_type.value if hasattr(s.test_type, "value") else s.test_type).upper() == required_test_type]

    if not matching:
        return CriterionOutcome(
            criterion="test",
            result=CriterionResult.FAIL,
            evidence=f"{required_test_type} is a mandatory requirement; no such test score is on file for the student.",
        )

    score_entry = matching[0]
    has_score = str(score_entry.status.value if hasattr(score_entry.status, "value") else score_entry.status) == "have"
    if not has_score:
        return CriterionOutcome(
            criterion="test",
            result=CriterionResult.FAIL,
            evidence=f"{required_test_type} is mandatory; student's status is {score_entry.status}, not yet taken.",
        )

    required_score = requirement.value
    if required_score is None or score_entry.score is None:
        return CriterionOutcome(
            criterion="test",
            result=CriterionResult.PASS,
            evidence=f"{required_test_type} is mandatory and student has taken it (no minimum score stated).",
        )

    if float(score_entry.score) >= float(required_score):
        return CriterionOutcome(
            criterion="test",
            result=CriterionResult.PASS,
            evidence=f"{required_test_type} requires >= {required_score}; student scored {score_entry.score}.",
        )
    return CriterionOutcome(
        criterion="test",
        result=CriterionResult.FAIL,
        evidence=f"{required_test_type} requires >= {required_score}; student scored {score_entry.score}.",
    )


_DIMENSION_EVALUATORS = {
    "gpa": lambda criterion, scholarship, profile: _evaluate_gpa(
        criterion, scholarship, [er.gpa for er in profile.education_records if er.gpa is not None]
    ),
    "nationality": lambda criterion, scholarship, profile: _evaluate_nationality(criterion, scholarship, profile.nationality),
    "degree": lambda criterion, scholarship, profile: _evaluate_degree(
        criterion, scholarship, profile.target_degree_level.value if profile.target_degree_level else None
    ),
    "test": lambda criterion, scholarship, profile: _evaluate_test(criterion, scholarship, profile.test_scores),
}


def evaluate_hard_constraints(profile, scholarship: Scholarship) -> list[CriterionOutcome]:
    """Evaluates every `hard_constraint`-kind criterion on the profile against
    the scholarship's structured data, plus the universal deadline check.
    Unsupported dimensions (no automated evaluator) surface as `unknown` —
    never silently dropped, never guessed."""
    outcomes = [_evaluate_deadline(scholarship)]

    for criterion in profile.criteria:
        if criterion.kind != CriterionKind.HARD_CONSTRAINT:
            continue

        dimension = str(criterion.dimension.value if hasattr(criterion.dimension, "value") else criterion.dimension)
        evaluator = _DIMENSION_EVALUATORS.get(dimension)
        if evaluator is None:
            outcomes.append(CriterionOutcome(criterion=dimension, result=CriterionResult.UNKNOWN))
            continue

        outcomes.append(evaluator(criterion, scholarship, profile))

    return outcomes


def any_hard_constraint_failed(outcomes: list[CriterionOutcome]) -> bool:
    return any(o.result == CriterionResult.FAIL for o in outcomes)
