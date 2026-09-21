"""Application data access — create/get (Resolution note 3), extended in
Phase 4 (T120) with plan/tracker/readiness queries. Every query is
`user_id`-scoped, matching `get_by_id_for_user`'s existing pattern; a
`submission_approvals` row has no direct `user_id` column (data-model.md §9),
so its queries scope by joining through `applications.user_id` instead.

The `submission_approvals` repository surface is INSERT (`create_submission_
approval`) + SELECT (`list_submission_approvals`) only, by design — no
update or delete method exists here (append-only, constitution Principle
III / FR-APP-3)."""

import uuid
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.application import Application, ReadinessLabel, SubmissionApproval, Task


def create(db: Session, user_id: uuid.UUID, scholarship_id: uuid.UUID) -> Application:
    application = Application(user_id=user_id, scholarship_id=scholarship_id)
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def get_by_id_for_user(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> Application | None:
    stmt = select(Application).where(
        Application.id == application_id, Application.user_id == user_id
    )
    return db.execute(stmt).scalar_one_or_none()


def list_for_user(db: Session, user_id: uuid.UUID) -> list[Application]:
    """Tracker (FR-TRACK-1): every application owned by `user_id`."""
    stmt = select(Application).where(Application.user_id == user_id)
    return list(db.execute(stmt).scalars().all())


def list_tasks(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> list[Task]:
    """The checklist (FR-PLAN-2) for one application, owned by `user_id`.
    Explicitly ordered (never relying on Postgres's unordered row return) so
    two reads of an unchanged checklist always render in the same order."""
    stmt = (
        select(Task)
        .where(Task.user_id == user_id, Task.application_id == application_id)
        .order_by(Task.category, Task.description, Task.id)
    )
    return list(db.execute(stmt).scalars().all())


def create_task(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    description: str,
    category: str,
    readiness_label: ReadinessLabel,
    due_date: date | None = None,
) -> Task:
    task = Task(
        user_id=user_id,
        application_id=application_id,
        description=description,
        category=category,
        readiness_label=readiness_label,
        due_date=due_date,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def replace_tasks_for_application(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    tasks: list[dict],
) -> list[Task]:
    """Full replace, never a partial update. `readiness_label` is a
    REGENERATED value (never hand-edited) — a re-run must produce the
    current checklist, not append a second copy alongside a stale one. The
    delete and the inserts share one transaction (single `commit()` at the
    end) so a partial replace is impossible: either the new set fully lands
    or the old set is left untouched.

    No general `update_task` / per-row label setter exists in this module by
    design — a label may only change via a full regeneration through this
    function, never an in-place edit."""
    db.execute(delete(Task).where(Task.user_id == user_id, Task.application_id == application_id))

    new_tasks = [
        Task(
            user_id=user_id,
            application_id=application_id,
            requirement_id=item.get("requirement_id"),
            description=item["description"],
            category=item["category"],
            readiness_label=item["readiness_label"],
            due_date=item.get("due_date"),
        )
        for item in tasks
    ]
    db.add_all(new_tasks)
    db.commit()
    for task in new_tasks:
        db.refresh(task)
    return new_tasks


def create_submission_approval(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    submission_scope: str,
    content_fingerprint: str,
    notes: str | None = None,
) -> SubmissionApproval | None:
    """INSERT only. `approved_by` is always `user_id` — the authenticated
    caller recording their own approval. Returns `None` (rather than
    inserting) when `application_id` is not owned by `user_id`, so an
    approval can never be recorded against another user's application."""
    application = get_by_id_for_user(db, user_id, application_id)
    if application is None:
        return None

    approval = SubmissionApproval(
        application_id=application_id,
        submission_scope=submission_scope,
        content_fingerprint=content_fingerprint,
        approved_by=user_id,
        notes=notes,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


def list_submission_approvals(db: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> list[SubmissionApproval]:
    """SELECT only — see module docstring."""
    stmt = (
        select(SubmissionApproval)
        .join(Application, SubmissionApproval.application_id == Application.id)
        .where(Application.id == application_id, Application.user_id == user_id)
    )
    return list(db.execute(stmt).scalars().all())
