"""Application Plan / Readiness / Assistant-step schemas (T125;
contracts/openapi.yaml `ReadinessLabel`/`ChecklistItem`/`ApplicationPlan`/
`AssistantStepResult`; data-model.md §8, FR-PLAN-2, SC-005).

`ReadinessLabel` is reused from `app.models.application` rather than
redefined here, mirroring `schemas/document.py`'s reuse of `DocumentType` —
the persisted `tasks.readiness_label` column and this response-layer schema
must never drift out of sync.

`ChecklistItem.readiness_label` is a single, non-optional `ReadinessLabel`
field — never a list, never `| None` — so "exactly one label" (FR-PLAN-2) is
enforced structurally by the type system at parse time, not by a runtime
validator that a caller could bypass.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.application import ReadinessLabel

__all__ = [
    "ReadinessLabel",
    "ChecklistItem",
    "ApplicationPlan",
    "AssistantStepResult",
    "SubmissionApprovalRequest",
    "SubmissionApprovalResponse",
]


class ChecklistItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    category: str
    readiness_label: ReadinessLabel
    due_date: date | None = None
    requirement_id: uuid.UUID | None = None


class ApplicationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    application_id: uuid.UUID
    checklist: list[ChecklistItem] = []


class AssistantStepResult(BaseModel):
    """Agentic-mode (`next-step`) result — FR-APP-2 requires this to be
    identical in shape to whatever the deterministic-mode endpoint it wraps
    returns, never a different response contract for the two entry points."""

    model_config = ConfigDict(frozen=True)

    step: str
    result: Any = None
    requires_user_input: bool = False
    requires_approval: bool = False


class SubmissionApprovalRequest(BaseModel):
    """`POST /applications/{id}/submission-approvals` request body
    (contracts/openapi.yaml). `submission_scope` identifies exactly which
    submission event this approval covers (FR-APP-3) -- never inferred."""

    submission_scope: str
    notes: str | None = None


class SubmissionApprovalResponse(BaseModel):
    """Response shape per contracts/openapi.yaml's `SubmissionApproval`
    schema, PLUS `content_fingerprint` (approved phase4c_design.md A3): the
    contract is silent, not explicit, on that field (no
    `additionalProperties: false`, no exclusion note like `FetchFailure.
    reported_as` or `CoverageSummary.claims_complete_coverage` carry) --
    including it lets the user see exactly what content they approved,
    which is the entire point of ADR-0004's scope+content-bound approval."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    application_id: uuid.UUID
    submission_scope: str
    content_fingerprint: str
    approved_by: uuid.UUID
    approved_at: datetime
