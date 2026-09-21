"""`readiness` service (T121; data-model.md §8, FR-PLAN-2, SC-005) — decides
the one `ReadinessLabel` a checklist item carries. Mirrors `hard_constraints`
and `requirement_satisfaction`'s deterministic style (constitution Principle
II, NON-NEGOTIABLE): a decision with one correct answer is code, not an LLM
judgment. `resolve_readiness_label` is PURE — no DB, no network, no LLM.

Material classification is decided by WHO PRODUCES the document, not by
whether a student is likely to have a personal copy on hand: an institution-
issued document (a transcript, a degree certificate, an employer's
experience letter) is `THIRD_PARTY_ISSUED` because obtaining a fresh,
official copy for *this* application is the `USER_MUST_OBTAIN` case the
`Task.readiness_label` docstring documents. `USER_HELD` is reserved for
material the student personally authored or compiled with no issuing
authority to return to (a portfolio, a publication list, a financial
statement they assembled themselves).

`unknown` satisfaction is never treated as `not_satisfied` (see
`requirement_satisfaction.py`): a document of the right type that is not
confirmed to satisfy a requirement routes to `NEEDS_HUMAN_REVIEW`, never to
an invented pass or fail.
"""

import uuid
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.data.repositories import document_repo, profile_repo, scholarship_repo
from app.models.application import Application, ReadinessLabel
from app.models.document import DocumentType, GeneratedDocumentType
from app.models.profile import TestScore
from app.models.requirement import Requirement, RequirementCategory

__all__ = [
    "MaterialClass",
    "ReadinessInput",
    "ChecklistItemDraft",
    "resolve_material",
    "resolve_material_class",
    "resolve_readiness_label",
    "compute_checklist_for_application",
]


class MaterialClass(StrEnum):
    SYSTEM_GENERATABLE = "system_generatable"
    USER_HELD = "user_held"
    THIRD_PARTY_ISSUED = "third_party_issued"


# Institution-issued materials: obtaining a fresh, official copy is the
# canonical USER_MUST_OBTAIN case, even though a student often has a
# personal copy already (data-model.md §8 Task docstring).
_THIRD_PARTY_ISSUED = {
    DocumentType.TRANSCRIPT,
    DocumentType.DEGREE_CERTIFICATE,
    DocumentType.CERTIFICATE,
    DocumentType.WORK_EXPERIENCE_DOC,
    DocumentType.LANGUAGE_TEST_DOC,
    DocumentType.GRE_GMAT_DOC,
    DocumentType.RECOMMENDATION_INFO,
    DocumentType.CHARACTER_CERTIFICATE,
    DocumentType.SCHOLARSHIP_SPECIFIC_FORM,
    DocumentType.UNIVERSITY_SPECIFIC_FORM,
}

# Student-authored/compiled material with no issuing authority to return to.
_USER_HELD = {
    DocumentType.PORTFOLIO,
    DocumentType.PUBLICATION,
    DocumentType.FINANCIAL_DOC,
}

# AI-draftable written material.
_SYSTEM_GENERATABLE = {
    DocumentType.CV,
    DocumentType.EUROPASS_CV,
    DocumentType.SOP,
    DocumentType.MOTIVATION_LETTER,
    DocumentType.PERSONAL_STATEMENT,
    DocumentType.STUDY_PLAN,
    DocumentType.RESEARCH_PROPOSAL,
}

_MATERIAL_CLASS_BY_TYPE: dict[DocumentType, MaterialClass] = {
    **{t: MaterialClass.THIRD_PARTY_ISSUED for t in _THIRD_PARTY_ISSUED},
    **{t: MaterialClass.USER_HELD for t in _USER_HELD},
    **{t: MaterialClass.SYSTEM_GENERATABLE for t in _SYSTEM_GENERATABLE},
}
# DocumentType.OTHER / UNCLASSIFIED are deliberately absent — unmapped.

# Category fallback used only when `requirement.key` doesn't directly match
# a DocumentType value. ELIGIBILITY, AGE, NATIONALITY, OTHER are
# deliberately absent — no document type represents them (FR-DOC-3: no
# uniform assumption), so they fall through to the catch-all label.
_CATEGORY_DEFAULT_MATERIAL: dict[RequirementCategory, DocumentType] = {
    RequirementCategory.ACADEMIC: DocumentType.TRANSCRIPT,
    RequirementCategory.GPA: DocumentType.TRANSCRIPT,
    RequirementCategory.LANGUAGE: DocumentType.LANGUAGE_TEST_DOC,
    RequirementCategory.TEST: DocumentType.GRE_GMAT_DOC,
    RequirementCategory.EXPERIENCE: DocumentType.WORK_EXPERIENCE_DOC,
    RequirementCategory.RESEARCH: DocumentType.RESEARCH_PROPOSAL,
}

# Only CV/SOP/MOTIVATION drafts are ever produced by an existing generation
# workflow (cv_gen/sop_gen) — personal_statement/study_plan/research_proposal
# have no reliable generated-type match (GeneratedDocument carries no
# requirement/material link), so they are absent here by design and simply
# never satisfy the "draft exists" rule.
_GENERATED_TYPE_BY_MATERIAL: dict[DocumentType, GeneratedDocumentType] = {
    DocumentType.CV: GeneratedDocumentType.CV,
    DocumentType.EUROPASS_CV: GeneratedDocumentType.CV,
    DocumentType.SOP: GeneratedDocumentType.SOP,
    DocumentType.MOTIVATION_LETTER: GeneratedDocumentType.MOTIVATION,
}

_LANGUAGE_TEST_CATEGORIES = (RequirementCategory.LANGUAGE, RequirementCategory.TEST)


def resolve_material(key: str, category: RequirementCategory) -> DocumentType | None:
    """`key` (scholarship-specific, e.g. "transcript") is tried first; a
    category default is used only when `key` doesn't match a `DocumentType`
    value. Returns None when neither resolves (unmapped)."""
    if key:
        try:
            return DocumentType(key.strip().lower())
        except ValueError:
            pass
    return _CATEGORY_DEFAULT_MATERIAL.get(category)


def resolve_material_class(material: DocumentType | None) -> MaterialClass | None:
    if material is None:
        return None
    return _MATERIAL_CLASS_BY_TYPE.get(material)


class ReadinessInput(BaseModel):
    """Everything `resolve_readiness_label` needs, pre-gathered from the DB.
    No requirement/application/document object crosses into the pure
    function — only these primitive/enum facts."""

    model_config = ConfigDict(frozen=True)

    value_status: str
    confidence: str
    material: DocumentType | None
    material_class: MaterialClass | None
    has_satisfying_document: bool
    has_matching_type_document: bool
    has_generated_draft: bool
    test_score_status: str | None = None


def resolve_readiness_label(item: ReadinessInput) -> ReadinessLabel:
    """PURE. Implements the approved A1 decision table, first match wins.
    Every branch returns a label — no path returns None, raises, or falls
    through (see phase4b_design.md A1 totality argument)."""
    if item.value_status in {"not_applicable", "conditional"}:
        return ReadinessLabel.NEEDS_HUMAN_REVIEW

    if item.value_status in {"unknown", "conflicting"} or item.confidence != "verified":
        return ReadinessLabel.NEEDS_OFFICIAL_VERIFICATION

    if item.has_satisfying_document:
        return ReadinessLabel.COMPLETE

    if item.material is not None and item.has_matching_type_document:
        return ReadinessLabel.NEEDS_HUMAN_REVIEW

    if item.material_class == MaterialClass.SYSTEM_GENERATABLE:
        if item.has_generated_draft:
            return ReadinessLabel.NEEDS_HUMAN_REVIEW
        return ReadinessLabel.AI_CAN_GENERATE

    if item.material_class == MaterialClass.USER_HELD:
        return ReadinessLabel.MISSING

    if item.material_class == MaterialClass.THIRD_PARTY_ISSUED:
        if item.test_score_status == "have":
            return ReadinessLabel.MISSING
        return ReadinessLabel.USER_MUST_OBTAIN

    return ReadinessLabel.NEEDS_HUMAN_REVIEW  # catch-all: material unresolved


class ChecklistItemDraft(BaseModel):
    """Pre-persistence checklist item: enough to sort (A3) and to build both
    a `tasks` row and a `ChecklistItem` response, without re-querying."""

    model_config = ConfigDict(frozen=True)

    requirement_id: uuid.UUID
    description: str
    category: str
    readiness_label: ReadinessLabel
    mandatory: bool
    due_date: date | None = None


def _matching_test_status(key: str, test_scores: list[TestScore]) -> str | None:
    """Mirrors `requirement_satisfaction._evaluate_test`'s matching logic
    (by test type, case-insensitive) without re-implementing satisfaction
    comparison — this only reads `TestScore.status`."""
    required = str(key).upper()
    matches = [
        ts
        for ts in test_scores
        if str(ts.test_type.value if hasattr(ts.test_type, "value") else ts.test_type).upper() == required
    ]
    if not matches:
        return None
    statuses = [str(ts.status.value if hasattr(ts.status, "value") else ts.status) for ts in matches]
    if "have" in statuses:
        return "have"
    return statuses[0]


def compute_checklist_for_application(
    db: Session, user_id: uuid.UUID, application: Application
) -> list[ChecklistItemDraft]:
    """Thin service function (T121): gathers per-application inputs via
    repositories and calls `resolve_readiness_label` once per stored
    `requirements` row — one checklist item per stored requirement, never a
    fabricated item (T122). Reuses the persisted `satisfies_requirement_id`
    link that the Phase 3C `requirement_satisfaction`/`doc_pipeline` services
    already computed, rather than re-implementing satisfaction logic here."""
    scholarship = scholarship_repo.get_by_id(db, application.scholarship_id)
    requirements: list[Requirement] = scholarship.requirements if scholarship is not None else []

    documents = document_repo.list_application_documents_for_application(db, user_id, application.id)
    generated_documents = document_repo.list_generated_documents_for_application(db, user_id, application.id)

    profile = profile_repo.get_by_user_id(db, user_id)
    test_scores = profile.test_scores if profile is not None else []

    drafts: list[ChecklistItemDraft] = []
    for requirement in requirements:
        material = resolve_material(requirement.key, requirement.category)
        material_class = resolve_material_class(material)

        has_satisfying_document = any(doc.satisfies_requirement_id == requirement.id for doc in documents)
        has_matching_type_document = material is not None and any(doc.type == material for doc in documents)

        generated_type = _GENERATED_TYPE_BY_MATERIAL.get(material) if material is not None else None
        has_generated_draft = generated_type is not None and any(
            gen.type == generated_type for gen in generated_documents
        )

        test_score_status = None
        if requirement.category in _LANGUAGE_TEST_CATEGORIES:
            test_score_status = _matching_test_status(requirement.key, test_scores)

        label = resolve_readiness_label(
            ReadinessInput(
                value_status=requirement.value_status,
                confidence=requirement.confidence,
                material=material,
                material_class=material_class,
                has_satisfying_document=has_satisfying_document,
                has_matching_type_document=has_matching_type_document,
                has_generated_draft=has_generated_draft,
                test_score_status=test_score_status,
            )
        )

        category_value = str(
            requirement.category.value if hasattr(requirement.category, "value") else requirement.category
        )
        drafts.append(
            ChecklistItemDraft(
                requirement_id=requirement.id,
                description=requirement.key,
                category=category_value,
                readiness_label=label,
                mandatory=requirement.mandatory,
            )
        )
    return drafts
