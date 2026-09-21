"""Submission-approval scope check (T116; ADR-0004, constitution Principle
III, FR-APP-3): the pure predicate deciding whether an already-stored
`SubmissionApproval` row authorizes a given submission attempt.

Both `submission_scope` AND `content_fingerprint` must match a stored row --
neither alone is sufficient. An approval for scope X never authorizes scope
Y; an approval for content A never authorizes changed content B even under
the same scope (ADR-0004's whole reason for adding the column). This is a
plain comparison over rows the caller already fetched (e.g. via
`application_repo.list_submission_approvals`) -- no DB access, no agent
memory, no prompt involved, so the same inputs always produce the same
answer.
"""

from app.models.application import SubmissionApproval

__all__ = ["is_submission_authorized"]


def is_submission_authorized(
    approvals: list[SubmissionApproval],
    submission_scope: str,
    content_fingerprint: str,
) -> bool:
    """True iff some row in `approvals` matches both `submission_scope` and
    `content_fingerprint` exactly."""
    return any(
        approval.submission_scope == submission_scope
        and approval.content_fingerprint == content_fingerprint
        for approval in approvals
    )
