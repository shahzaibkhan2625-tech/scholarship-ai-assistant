"""Requirement-satisfaction verdict-correctness test matrix (T105 supporting
piece; mirrors `tests/services/test_hard_constraints_matrix.py`'s style): a
fixed set of (extracted document fields x scholarship requirement) pairs ->
expected satisfaction status. A requirement with no comparable information
on either side MUST come back `unknown`, never collapsed into
`not_satisfied` — the two lead to different user actions (upload more
info vs. genuinely does not qualify).

Pure unit tests: transient ORM objects only, no DB, no LLM."""

import uuid

import pytest

from app.models.requirement import Requirement, RequirementCategory
from app.services.requirement_satisfaction import SatisfactionStatus, evaluate_requirement
from app.tools.extract_document_fields import ExtractedDocumentFields, ExtractedTestScore


def _requirement(**kwargs) -> Requirement:
    kwargs.setdefault("value_status", "known")
    kwargs.setdefault("confidence", "verified")
    kwargs.setdefault("mandatory", True)
    return Requirement(id=uuid.uuid4(), scholarship_id=uuid.uuid4(), **kwargs)


# --- Matrix cases: (extracted, requirement, expected_status) ---


def case_gpa_satisfied():
    extracted = ExtractedDocumentFields(gpa=3.8, gpa_scale=4.0, value_status="known")
    requirement = _requirement(category=RequirementCategory.GPA, key="min_gpa", value=3.5)
    return extracted, requirement, SatisfactionStatus.SATISFIED


def case_gpa_not_satisfied():
    extracted = ExtractedDocumentFields(gpa=3.0, gpa_scale=4.0, value_status="known")
    requirement = _requirement(category=RequirementCategory.GPA, key="min_gpa", value=3.5)
    return extracted, requirement, SatisfactionStatus.NOT_SATISFIED


def case_gpa_normalizes_a_different_scale():
    """3.2/4.0 must not be compared directly against an 8/10 requirement."""
    extracted = ExtractedDocumentFields(gpa=8.0, gpa_scale=10.0, value_status="known")  # -> 3.2/4.0
    requirement = _requirement(category=RequirementCategory.GPA, key="min_gpa", value=3.5)
    return extracted, requirement, SatisfactionStatus.NOT_SATISFIED


def case_gpa_unknown_when_requirement_unstated():
    """US2-style scenario applied to documents: a scholarship that doesn't
    state a GPA requirement at all is unknown, never assumed pass/fail."""
    extracted = ExtractedDocumentFields(gpa=3.8, value_status="known")
    requirement = _requirement(category=RequirementCategory.GPA, key="min_gpa", value=None, value_status="unknown")
    return extracted, requirement, SatisfactionStatus.UNKNOWN


def case_gpa_unknown_when_document_missing_field():
    """A missing field on the DOCUMENT side yields unknown, never
    not_satisfied — the two lead to different user actions."""
    extracted = ExtractedDocumentFields(gpa=None, value_status="unknown")
    requirement = _requirement(category=RequirementCategory.GPA, key="min_gpa", value=3.5)
    return extracted, requirement, SatisfactionStatus.UNKNOWN


def case_test_satisfied():
    extracted = ExtractedDocumentFields(
        test_scores=[ExtractedTestScore(test_type="IELTS", score=7.0)], value_status="known"
    )
    requirement = _requirement(category=RequirementCategory.TEST, key="IELTS", value=6.5)
    return extracted, requirement, SatisfactionStatus.SATISFIED


def case_test_not_satisfied():
    extracted = ExtractedDocumentFields(
        test_scores=[ExtractedTestScore(test_type="IELTS", score=5.5)], value_status="known"
    )
    requirement = _requirement(category=RequirementCategory.TEST, key="IELTS", value=6.5)
    return extracted, requirement, SatisfactionStatus.NOT_SATISFIED


def case_test_unknown_when_document_has_no_matching_score():
    extracted = ExtractedDocumentFields(test_scores=[], value_status="known")
    requirement = _requirement(category=RequirementCategory.TEST, key="IELTS", value=6.5)
    return extracted, requirement, SatisfactionStatus.UNKNOWN


def case_academic_satisfied():
    extracted = ExtractedDocumentFields(degree="Master of Science", value_status="known")
    requirement = _requirement(category=RequirementCategory.ACADEMIC, key="degree", value="Master of Science")
    return extracted, requirement, SatisfactionStatus.SATISFIED


def case_academic_not_satisfied():
    extracted = ExtractedDocumentFields(degree="Bachelor of Arts", value_status="known")
    requirement = _requirement(category=RequirementCategory.ACADEMIC, key="degree", value="Master of Science")
    return extracted, requirement, SatisfactionStatus.NOT_SATISFIED


def case_unsupported_category_is_unknown():
    """A category with no automated evaluator (e.g. nationality on a
    document check) always surfaces unknown — never guessed."""
    extracted = ExtractedDocumentFields(value_status="known")
    requirement = _requirement(
        category=RequirementCategory.NATIONALITY, key="eligible_nationalities", value=["Pakistani"]
    )
    return extracted, requirement, SatisfactionStatus.UNKNOWN


_MATRIX = [
    case_gpa_satisfied,
    case_gpa_not_satisfied,
    case_gpa_normalizes_a_different_scale,
    case_gpa_unknown_when_requirement_unstated,
    case_gpa_unknown_when_document_missing_field,
    case_test_satisfied,
    case_test_not_satisfied,
    case_test_unknown_when_document_has_no_matching_score,
    case_academic_satisfied,
    case_academic_not_satisfied,
    case_unsupported_category_is_unknown,
]


@pytest.mark.parametrize("case_factory", _MATRIX, ids=[c.__name__ for c in _MATRIX])
def test_satisfaction_matrix(case_factory) -> None:
    extracted, requirement, expected_status = case_factory()
    outcome = evaluate_requirement(extracted, requirement)
    assert outcome.status == expected_status
