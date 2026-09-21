"""Readiness-label verdict-correctness test matrix (T121; mirrors
`tests/services/test_hard_constraints_matrix.py` / `test_requirement_
satisfaction_matrix.py`'s style): a fixed set of `ReadinessInput`s -> the one
expected `ReadinessLabel`, covering every row of the approved A1 decision
table (docs/phase4b_design.md), first-match-wins, in order.

Pure unit tests: transient `ReadinessInput` objects only, no DB, no LLM.
"""

import itertools

import pytest

from app.models.application import ReadinessLabel
from app.models.document import DocumentType
from app.services.readiness import MaterialClass, ReadinessInput, resolve_readiness_label


def _input(**kwargs) -> ReadinessInput:
    kwargs.setdefault("value_status", "known")
    kwargs.setdefault("confidence", "verified")
    kwargs.setdefault("material", None)
    kwargs.setdefault("material_class", None)
    kwargs.setdefault("has_satisfying_document", False)
    kwargs.setdefault("has_matching_type_document", False)
    kwargs.setdefault("has_generated_draft", False)
    kwargs.setdefault("test_score_status", None)
    return ReadinessInput(**kwargs)


# --- Matrix cases: (readiness_input, expected_label) ---


def case_not_applicable_needs_human_review():
    return _input(value_status="not_applicable"), ReadinessLabel.NEEDS_HUMAN_REVIEW


def case_conditional_needs_human_review():
    return _input(value_status="conditional"), ReadinessLabel.NEEDS_HUMAN_REVIEW


def case_unknown_needs_official_verification():
    return _input(value_status="unknown"), ReadinessLabel.NEEDS_OFFICIAL_VERIFICATION


def case_conflicting_needs_official_verification():
    return _input(value_status="conflicting"), ReadinessLabel.NEEDS_OFFICIAL_VERIFICATION


def case_unverified_confidence_needs_official_verification():
    """`known` + non-`verified` confidence must not be treated as ready to
    check against — only an official-source-confirmed fact clears row 2."""
    return _input(value_status="known", confidence="inferred"), ReadinessLabel.NEEDS_OFFICIAL_VERIFICATION


def case_satisfying_document_is_complete():
    """Row 3 fires independently of `material` resolution — an unmapped
    category that is nonetheless linked to a satisfying document is still
    COMPLETE, never the row-9 catch-all."""
    return (
        _input(material=None, material_class=None, has_satisfying_document=True),
        ReadinessLabel.COMPLETE,
    )


def case_matching_but_unresolved_document_needs_human_review():
    return (
        _input(
            material=DocumentType.TRANSCRIPT,
            material_class=MaterialClass.THIRD_PARTY_ISSUED,
            has_matching_type_document=True,
            has_satisfying_document=False,
        ),
        ReadinessLabel.NEEDS_HUMAN_REVIEW,
    )


def case_generatable_with_draft_needs_human_review():
    return (
        _input(
            material=DocumentType.SOP,
            material_class=MaterialClass.SYSTEM_GENERATABLE,
            has_generated_draft=True,
        ),
        ReadinessLabel.NEEDS_HUMAN_REVIEW,
    )


def case_generatable_without_draft_is_ai_can_generate():
    return (
        _input(material=DocumentType.SOP, material_class=MaterialClass.SYSTEM_GENERATABLE),
        ReadinessLabel.AI_CAN_GENERATE,
    )


def case_user_held_not_uploaded_is_missing():
    return (
        _input(material=DocumentType.PORTFOLIO, material_class=MaterialClass.USER_HELD),
        ReadinessLabel.MISSING,
    )


def case_third_party_issued_with_test_score_have_is_missing():
    """The student already holds the score — they just haven't uploaded it;
    never wrongly told to go obtain a new one."""
    return (
        _input(
            material=DocumentType.LANGUAGE_TEST_DOC,
            material_class=MaterialClass.THIRD_PARTY_ISSUED,
            test_score_status="have",
        ),
        ReadinessLabel.MISSING,
    )


def case_third_party_issued_without_test_score_is_user_must_obtain():
    return (
        _input(material=DocumentType.TRANSCRIPT, material_class=MaterialClass.THIRD_PARTY_ISSUED),
        ReadinessLabel.USER_MUST_OBTAIN,
    )


def case_third_party_issued_test_score_planned_is_user_must_obtain():
    return (
        _input(
            material=DocumentType.GRE_GMAT_DOC,
            material_class=MaterialClass.THIRD_PARTY_ISSUED,
            test_score_status="planned",
        ),
        ReadinessLabel.USER_MUST_OBTAIN,
    )


def case_unmapped_material_is_needs_human_review_catch_all():
    return _input(material=None, material_class=None), ReadinessLabel.NEEDS_HUMAN_REVIEW


_MATRIX = [
    case_not_applicable_needs_human_review,
    case_conditional_needs_human_review,
    case_unknown_needs_official_verification,
    case_conflicting_needs_official_verification,
    case_unverified_confidence_needs_official_verification,
    case_satisfying_document_is_complete,
    case_matching_but_unresolved_document_needs_human_review,
    case_generatable_with_draft_needs_human_review,
    case_generatable_without_draft_is_ai_can_generate,
    case_user_held_not_uploaded_is_missing,
    case_third_party_issued_with_test_score_have_is_missing,
    case_third_party_issued_without_test_score_is_user_must_obtain,
    case_third_party_issued_test_score_planned_is_user_must_obtain,
    case_unmapped_material_is_needs_human_review_catch_all,
]


@pytest.mark.parametrize("case_factory", _MATRIX, ids=[c.__name__ for c in _MATRIX])
def test_readiness_matrix(case_factory) -> None:
    readiness_input, expected_label = case_factory()
    assert resolve_readiness_label(readiness_input) == expected_label


def test_readiness_label_is_total_over_every_input_combination() -> None:
    """No combination of inputs may return None, raise, or fall through
    un-labeled — every path in the A1 table terminates in exactly one of the
    six ReadinessLabel values, including the row-9 catch-all."""
    value_statuses = ["known", "unknown", "not_applicable", "conditional", "conflicting"]
    confidences = ["verified", "inferred", "unknown"]
    material_classes = [None, *list(MaterialClass)]
    bool_flags = [False, True]
    test_score_statuses = [None, "have", "planned", "none"]

    seen_labels: set[ReadinessLabel] = set()
    for value_status, confidence, material_class, satisfying, matching, draft, test_status in itertools.product(
        value_statuses, confidences, material_classes, bool_flags, bool_flags, bool_flags, test_score_statuses
    ):
        material = DocumentType.TRANSCRIPT if material_class is not None else None
        readiness_input = ReadinessInput(
            value_status=value_status,
            confidence=confidence,
            material=material,
            material_class=material_class,
            has_satisfying_document=satisfying,
            has_matching_type_document=matching,
            has_generated_draft=draft,
            test_score_status=test_status,
        )
        label = resolve_readiness_label(readiness_input)
        assert isinstance(label, ReadinessLabel)
        seen_labels.add(label)

    # The catch-all (material/material_class unresolved) must itself be
    # reachable, not merely theoretical.
    catch_all_input = ReadinessInput(
        value_status="known",
        confidence="verified",
        material=None,
        material_class=None,
        has_satisfying_document=False,
        has_matching_type_document=False,
        has_generated_draft=False,
        test_score_status=None,
    )
    assert resolve_readiness_label(catch_all_input) == ReadinessLabel.NEEDS_HUMAN_REVIEW
    assert ReadinessLabel.NEEDS_HUMAN_REVIEW in seen_labels
