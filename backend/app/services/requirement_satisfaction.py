"""`requirement_satisfaction` service — deterministic comparison of
extracted document fields against a scholarship's requirements. NO LLM
INVOLVEMENT ANYWHERE IN THIS MODULE (constitution Principle II,
NON-NEGOTIABLE): whether an extracted value satisfies a requirement is a
computation, not a judgement. Mirrors `hard_constraints`'s deterministic
style — this module's output is consumed read-only by `doc_pipeline`.

Every requirement check returns exactly one of satisfied / not_satisfied /
unknown. "unknown" means the document (or the requirement itself) did not
state a comparable value and MUST NOT collapse into not_satisfied — the two
lead to different user actions (upload more info vs. genuinely does not
qualify).

`detect_profile_inconsistencies` is a separate, equally deterministic check:
it compares extracted document fields against the user's existing profile
and flags a genuine disagreement (both sides have a value AND they differ)
without ever silently picking a winner or overwriting the profile."""

import uuid
from enum import StrEnum

from pydantic import BaseModel

from app.models.requirement import Requirement
from app.tools.extract_document_fields import ExtractedDocumentFields

__all__ = [
    "SatisfactionStatus",
    "SatisfactionOutcome",
    "InconsistencyFlag",
    "evaluate_requirement",
    "evaluate_requirements",
    "detect_profile_inconsistencies",
]


class SatisfactionStatus(StrEnum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    UNKNOWN = "unknown"


class SatisfactionOutcome(BaseModel):
    requirement_id: uuid.UUID
    category: str
    status: SatisfactionStatus
    evidence: str | None = None


class InconsistencyFlag(BaseModel):
    type: str = "profile_mismatch"
    field: str
    profile_value: object
    document_value: object


def _category_of(requirement: Requirement) -> str:
    return str(requirement.category.value if hasattr(requirement.category, "value") else requirement.category)


def _requirement_known(requirement: Requirement) -> bool:
    return requirement.value_status == "known" and requirement.value is not None


def _evaluate_gpa(extracted: ExtractedDocumentFields, requirement: Requirement) -> SatisfactionOutcome:
    category = _category_of(requirement)
    if not _requirement_known(requirement) or extracted.gpa is None:
        return SatisfactionOutcome(requirement_id=requirement.id, category=category, status=SatisfactionStatus.UNKNOWN)

    required_gpa = float(requirement.value)
    document_gpa = float(extracted.gpa)
    # Normalize to a 4.0 scale when the document states a different scale, so
    # e.g. a 3.2/4.0 is never compared directly against an 8/10 requirement.
    if extracted.gpa_scale:
        document_gpa = document_gpa / float(extracted.gpa_scale) * 4.0

    if document_gpa >= required_gpa:
        return SatisfactionOutcome(
            requirement_id=requirement.id,
            category=category,
            status=SatisfactionStatus.SATISFIED,
            evidence=f"Requirement is GPA >= {required_gpa}; document states {extracted.gpa} (normalized {document_gpa:.2f}).",
        )
    return SatisfactionOutcome(
        requirement_id=requirement.id,
        category=category,
        status=SatisfactionStatus.NOT_SATISFIED,
        evidence=f"Requirement is GPA >= {required_gpa}; document states {extracted.gpa} (normalized {document_gpa:.2f}).",
    )


def _evaluate_test(extracted: ExtractedDocumentFields, requirement: Requirement) -> SatisfactionOutcome:
    category = _category_of(requirement)
    if not _requirement_known(requirement):
        return SatisfactionOutcome(requirement_id=requirement.id, category=category, status=SatisfactionStatus.UNKNOWN)

    required_test_type = str(requirement.key).upper()
    matching = [s for s in extracted.test_scores if s.test_type.upper() == required_test_type]
    if not matching or matching[0].score is None:
        return SatisfactionOutcome(requirement_id=requirement.id, category=category, status=SatisfactionStatus.UNKNOWN)

    required_score = requirement.value
    score = matching[0].score
    if required_score is None:
        return SatisfactionOutcome(
            requirement_id=requirement.id,
            category=category,
            status=SatisfactionStatus.SATISFIED,
            evidence=f"{required_test_type} is required (no minimum stated); document states a score of {score}.",
        )
    if float(score) >= float(required_score):
        return SatisfactionOutcome(
            requirement_id=requirement.id,
            category=category,
            status=SatisfactionStatus.SATISFIED,
            evidence=f"{required_test_type} requires >= {required_score}; document states {score}.",
        )
    return SatisfactionOutcome(
        requirement_id=requirement.id,
        category=category,
        status=SatisfactionStatus.NOT_SATISFIED,
        evidence=f"{required_test_type} requires >= {required_score}; document states {score}.",
    )


def _evaluate_academic(extracted: ExtractedDocumentFields, requirement: Requirement) -> SatisfactionOutcome:
    category = _category_of(requirement)
    if not _requirement_known(requirement) or not extracted.degree:
        return SatisfactionOutcome(requirement_id=requirement.id, category=category, status=SatisfactionStatus.UNKNOWN)

    required_degree = str(requirement.value).strip().lower()
    document_degree = extracted.degree.strip().lower()
    if required_degree in document_degree or document_degree in required_degree:
        return SatisfactionOutcome(
            requirement_id=requirement.id,
            category=category,
            status=SatisfactionStatus.SATISFIED,
            evidence=f"Requirement expects degree '{requirement.value}'; document states '{extracted.degree}'.",
        )
    return SatisfactionOutcome(
        requirement_id=requirement.id,
        category=category,
        status=SatisfactionStatus.NOT_SATISFIED,
        evidence=f"Requirement expects degree '{requirement.value}'; document states '{extracted.degree}'.",
    )


_CATEGORY_EVALUATORS = {
    "gpa": _evaluate_gpa,
    "test": _evaluate_test,
    "academic": _evaluate_academic,
}


def evaluate_requirement(extracted: ExtractedDocumentFields, requirement: Requirement) -> SatisfactionOutcome:
    """Evaluates one requirement against extracted document fields. A
    category with no automated evaluator always surfaces `unknown` — never
    guessed, never silently dropped."""
    category = _category_of(requirement)
    evaluator = _CATEGORY_EVALUATORS.get(category)
    if evaluator is None:
        return SatisfactionOutcome(requirement_id=requirement.id, category=category, status=SatisfactionStatus.UNKNOWN)
    return evaluator(extracted, requirement)


def evaluate_requirements(
    extracted: ExtractedDocumentFields, requirements: list[Requirement]
) -> list[SatisfactionOutcome]:
    return [evaluate_requirement(extracted, requirement) for requirement in requirements]


def detect_profile_inconsistencies(extracted: ExtractedDocumentFields, profile) -> list[InconsistencyFlag]:
    """Deterministic comparison of extracted document fields against the
    user's existing profile. Only a genuine disagreement (both sides have a
    value AND they differ) is flagged — a missing value on either side is
    never treated as a contradiction (constitution Principle I: never
    silently pick a winner between the two, and never invent a conflict that
    isn't there)."""
    flags: list[InconsistencyFlag] = []

    profile_gpas = [float(er.gpa) for er in profile.education_records if er.gpa is not None]
    if extracted.gpa is not None and profile_gpas:
        best_profile_gpa = max(profile_gpas)
        if abs(best_profile_gpa - float(extracted.gpa)) > 0.01:
            flags.append(
                InconsistencyFlag(field="gpa", profile_value=best_profile_gpa, document_value=extracted.gpa)
            )

    profile_institutions = {er.university for er in profile.education_records if er.university}
    if extracted.institution and profile_institutions and extracted.institution not in profile_institutions:
        flags.append(
            InconsistencyFlag(
                field="institution",
                profile_value=sorted(profile_institutions),
                document_value=extracted.institution,
            )
        )

    for score in extracted.test_scores:
        if score.score is None:
            continue
        matching_profile_scores = [
            ts
            for ts in profile.test_scores
            if str(ts.test_type.value if hasattr(ts.test_type, "value") else ts.test_type).upper()
            == score.test_type.upper()
            and ts.score is not None
        ]
        for ts in matching_profile_scores:
            if abs(float(ts.score) - float(score.score)) > 0.01:
                flags.append(
                    InconsistencyFlag(
                        field=f"test_score_{score.test_type.lower()}",
                        profile_value=float(ts.score),
                        document_value=score.score,
                    )
                )

    return flags
