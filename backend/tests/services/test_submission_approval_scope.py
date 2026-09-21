"""`is_submission_authorized` scope-check tests (T116; ADR-0004, constitution
Principle III, FR-APP-3) -- the core safety predicate of this slice: a stored
`SubmissionApproval` authorizes a submission attempt only when BOTH its
`submission_scope` and `content_fingerprint` match exactly.

Pure unit tests: transient `SubmissionApproval` objects only (never
persisted), no DB, no LLM -- mirrors `test_readiness_matrix.py`'s style for
testing a pure service function in isolation.
"""

import uuid

from app.models.application import SubmissionApproval
from app.services.submission_approval import is_submission_authorized


def _approval(submission_scope: str, content_fingerprint: str) -> SubmissionApproval:
    return SubmissionApproval(
        id=uuid.uuid4(),
        application_id=uuid.uuid4(),
        submission_scope=submission_scope,
        content_fingerprint=content_fingerprint,
        approved_by=uuid.uuid4(),
    )


def test_same_scope_and_same_fingerprint_is_authorized() -> None:
    approvals = [_approval("final-submission", "abc123")]

    assert is_submission_authorized(approvals, "final-submission", "abc123") is True


def test_different_scope_is_not_authorized() -> None:
    """An approval for scope X never authorizes scope Y, even with the exact
    same content fingerprint."""
    approvals = [_approval("final-submission", "abc123")]

    assert is_submission_authorized(approvals, "resubmission-after-deadline-extension", "abc123") is False


def test_same_scope_with_changed_content_is_not_authorized() -> None:
    """An approval for content A never authorizes changed content B under
    the same scope -- this is precisely the gap ADR-0004 closed."""
    approvals = [_approval("final-submission", "abc123")]

    assert is_submission_authorized(approvals, "final-submission", "def456") is False


def test_no_stored_approvals_is_not_authorized() -> None:
    assert is_submission_authorized([], "final-submission", "abc123") is False


def test_matching_row_among_other_non_matching_rows_is_authorized() -> None:
    approvals = [
        _approval("draft-review", "abc123"),
        _approval("final-submission", "def456"),
        _approval("final-submission", "abc123"),
    ]

    assert is_submission_authorized(approvals, "final-submission", "abc123") is True
