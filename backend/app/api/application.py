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

from app.api.deps import get_current_user
from app.data.repositories import application_repo, scholarship_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.document import ApplicationCreate, ApplicationRead
from app.schemas.plan import ApplicationPlan, ChecklistItem
from app.workflows.app_plan.graph import run_app_plan

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
