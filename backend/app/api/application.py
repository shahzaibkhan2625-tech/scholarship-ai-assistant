"""Application API (T107, T126; contracts/openapi.yaml `POST /applications`,
`GET /applications`, `POST /applications/{id}/plan`). `POST /applications`
shipped in Phase 3 (T107); the tracker and plan endpoints below are Phase 4
(T126) additions to this same router file, per Resolution note 3 in
tasks.md.

`user_id` is always loaded server-side from the authenticated token, mirroring
`api/discovery.py`'s current-user pattern — never accepted in the request
body.

Neither Phase 4 endpoint contains any planning or labelling logic. `GET
/applications` only reads the already-persisted checklist (via
`application_repo.list_tasks`); `POST /applications/{id}/plan` only calls
`run_app_plan` — the single shared entry point the future agentic mode (T124)
will also call. Logic here would be logic the agent could not share."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agents.application.agent import run_application_assistant
from app.api.deps import get_current_user
from app.data.repositories import application_repo, document_repo, scholarship_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.document import ApplicationCreate, ApplicationRead
from app.schemas.plan import (
    ApplicationPlan,
    AssistantStepResult,
    ChecklistItem,
    SubmissionApprovalRequest,
    SubmissionApprovalResponse,
)
from app.workflows.app_plan.graph import run_app_plan
from app.workflows.submit_prep.graph import compute_content_fingerprint

router = APIRouter()


@router.post("/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def create_application(
    body: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationRead:
    scholarship = scholarship_repo.get_by_id(db, body.scholarship_id)
    if scholarship is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scholarship not found")

    application = application_repo.create(db, current_user.id, body.scholarship_id)
    return ApplicationRead.model_validate(application)


@router.get("/applications", response_model=list[ApplicationRead])
def list_applications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApplicationRead]:
    """Tracker (FR-TRACK-1): the caller's own applications only, each
    carrying its already-persisted readiness checklist. Read-only — it never
    triggers plan generation; that is `POST /applications/{id}/plan`."""
    applications = application_repo.list_for_user(db, current_user.id)
    results: list[ApplicationRead] = []
    for application in applications:
        tasks = application_repo.list_tasks(db, current_user.id, application.id)
        checklist = [
            ChecklistItem(
                description=task.description,
                category=task.category,
                readiness_label=task.readiness_label,
                due_date=task.due_date,
                requirement_id=task.requirement_id,
            )
            for task in tasks
        ]
        results.append(
            ApplicationRead(
                id=application.id,
                scholarship_id=application.scholarship_id,
                status=application.status,
                created_at=application.created_at,
                checklist=checklist,
            )
        )
    return results


@router.post("/applications/{application_id}/plan", response_model=ApplicationPlan)
def generate_application_plan(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplicationPlan:
    """Deterministic-mode button: generates/refreshes the labeled checklist.
    Same `run_app_plan` call the future agentic mode (T124) will use."""
    plan = run_app_plan(db, current_user.id, application_id)
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return plan


@router.post("/applications/{application_id}/assistant/next-step", response_model=AssistantStepResult)
def get_assistant_next_step(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AssistantStepResult:
    """Agentic mode (T124): determines and executes the next step by calling
    `run_application_assistant` -- no planning/labelling/fingerprinting logic
    here, that all lives in the shared `run_app_plan`/`run_submit_prep`
    workflows the agent itself calls."""
    result = run_application_assistant(db, current_user.id, application_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return result


@router.post(
    "/applications/{application_id}/submission-approvals",
    response_model=SubmissionApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_application_submission_approval(
    application_id: uuid.UUID,
    body: SubmissionApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SubmissionApprovalResponse:
    """Records an explicit, per-submission human approval (FR-APP-3,
    constitution Principle III). `content_fingerprint` is computed here via
    the SAME `compute_content_fingerprint` `submit_prep` uses -- never
    reimplemented -- so an approval is bound to the exact content it was
    given for."""
    application = application_repo.get_by_id_for_user(db, current_user.id, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    application_documents = document_repo.list_application_documents_for_application(
        db, current_user.id, application_id
    )
    generated_documents = document_repo.list_generated_documents_for_application(
        db, current_user.id, application_id
    )
    content_fingerprint = compute_content_fingerprint(application_documents, generated_documents)

    approval = application_repo.create_submission_approval(
        db, current_user.id, application_id, body.submission_scope, content_fingerprint, body.notes
    )
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return SubmissionApprovalResponse.model_validate(approval)
