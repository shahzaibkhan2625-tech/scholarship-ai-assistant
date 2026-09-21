"""Application/Submission assistant agent (T124; FR-APP-1/2, US6, approved
phase4c_design.md A1/A2).

NO LLM CALL ANYWHERE IN THIS MODULE. The "next step" decision is a pure,
deterministic rule table over the freshly-recomputed checklist plus the
stored `submission_approvals` rows -- the same style as
`readiness.resolve_readiness_label`. This is what makes the agentic mode
(this module) and the deterministic-mode buttons (`POST .../plan`,
`POST .../submission-approvals`) provably consistent: this agent contains NO
reimplementation of readiness labelling, checklist assembly, or
fingerprinting -- it calls `run_app_plan` and `run_submit_prep` directly,
the exact same shared entry points those buttons call/rely on.

`run_application_assistant` is the ONLY public entry point (mirrors
`run_app_plan`/`run_submit_prep`'s one-function shape) and the ONLY function
`app/api/application.py`'s `next-step` endpoint and
`app/orchestration/main_agent.py`'s dispatcher may call.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.application import ReadinessLabel
from app.schemas.plan import AssistantStepResult
from app.workflows.app_plan.graph import run_app_plan
from app.workflows.submit_prep.graph import run_submit_prep

__all__ = ["DEFAULT_SUBMISSION_SCOPE", "run_application_assistant"]

# Fixed, never guessed per-call: `is_submission_authorized` requires an EXACT
# scope match against whatever the user actually approves via
# `POST /applications/{id}/submission-approvals`, so the guided flow's
# default scope must be a constant, not invented per invocation. Matches the
# literal already used by the T114/T116 test fixtures for the single
# canonical submission event this slice drives. A differently-scoped
# resubmission is a distinct future capability, not something this agent
# should silently choose.
DEFAULT_SUBMISSION_SCOPE = "final-submission"

# MISSING, USER_MUST_OBTAIN, AI_CAN_GENERATE are all user-actionable today
# (upload, obtain-then-upload, or POST .../generate/cv|sop) -- blocking on
# them gives the user a real next action. NEEDS_HUMAN_REVIEW and
# NEEDS_OFFICIAL_VERIFICATION have no actionable endpoint yet, so blocking on
# either would be a dead end; they get human eyes at the submission-approval
# gate instead (approved A1).
_BLOCKING_LABELS = frozenset(
    {ReadinessLabel.MISSING, ReadinessLabel.USER_MUST_OBTAIN, ReadinessLabel.AI_CAN_GENERATE}
)


def run_application_assistant(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> AssistantStepResult | None:
    """Returns `None` when `application_id` is not owned by `user_id` (or
    doesn't exist) -- same convention as `run_app_plan`/`run_submit_prep`;
    the caller maps that to 404.

    Always recomputes the checklist fresh via `run_app_plan` (never reads
    potentially-stale `tasks` rows directly) so this function and the
    `POST .../plan` button can never diverge on the same underlying state
    (US6 Acceptance Scenario 5 parity)."""
    plan = run_app_plan(db, user_id, application_id)
    if plan is None:
        return None

    blocking = [item for item in plan.checklist if item.readiness_label in _BLOCKING_LABELS]
    if blocking:
        return AssistantStepResult(
            step="action_required",
            result={
                "blocking_tasks": [
                    {
                        "description": item.description,
                        "category": item.category,
                        "readiness_label": item.readiness_label,
                    }
                    for item in blocking
                ]
            },
            requires_user_input=True,
            requires_approval=False,
        )

    staged_result = run_submit_prep(db, user_id, application_id, submission_scope=DEFAULT_SUBMISSION_SCOPE)
    # `staged_result` is never None here: `run_app_plan` above already proved
    # `application_id` is owned by `user_id` and exists.

    if staged_result["authorized"]:
        return AssistantStepResult(
            step="ready_to_submit",
            result={
                "staged": staged_result["staged"],
                "content_fingerprint": staged_result["content_fingerprint"],
            },
            requires_user_input=False,
            requires_approval=False,
        )

    return AssistantStepResult(
        step="awaiting_submission_approval",
        result={
            "submission_scope": DEFAULT_SUBMISSION_SCOPE,
            "content_fingerprint": staged_result["content_fingerprint"],
        },
        requires_user_input=False,
        requires_approval=True,
    )
