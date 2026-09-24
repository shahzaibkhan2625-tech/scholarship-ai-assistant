"""Generation API (T111; contracts/openapi.yaml `POST
/applications/{id}/generate/cv`, `POST /applications/{id}/generate/sop`).
Mirrors `api/documents.py`'s pattern: `user_id` is always loaded server-side
from the authenticated token, application ownership is verified (404
otherwise), and all business logic lives in the `cv_gen`/`sop_gen`
workflows — this router only wires them to HTTP, with no SQL/session queries
of its own.

A blocked workflow run (an untraceable claim, or nothing to draft from) maps
to `409` naming the unresolved gaps — never a partial document with the
ungrounded parts silently removed, and never invented content to fill the
gap (US5 Scenario 5, FR-GEN-2)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.repositories import application_repo, document_repo, scholarship_repo
from app.data.repositories.db import get_db
from app.models.user import User
from app.schemas.document import GeneratedDocument as GeneratedDocumentSchema
from app.services.profile import get_or_create_profile
from app.workflows.cv_gen.graph import run_cv_gen
from app.workflows.sop_gen.graph import run_sop_gen

router = APIRouter()


def _gap_detail(gaps: list[dict]) -> dict:
    return {
        "message": (
            "Generation halted: one or more claims could not be traced to the "
            "profile, an uploaded document, or a verified scholarship requirement."
        ),
        "gaps": gaps,
    }


def _get_owned_application(db: Session, current_user: User, application_id: uuid.UUID):
    application = application_repo.get_by_id_for_user(db, current_user.id, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")
    return application


@router.post(
    "/applications/{application_id}/generate/cv",
    response_model=GeneratedDocumentSchema,
    responses={409: {"description": "Gap in required information — reported, not filled with invented content (US5 Scenario 5)"}},
)
def generate_cv(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeneratedDocumentSchema:
    application = _get_owned_application(db, current_user, application_id)

    profile = get_or_create_profile(db, current_user.id)
    documents = document_repo.list_application_documents_for_application(db, current_user.id, application_id)
    scholarship = scholarship_repo.get_by_id(db, application.scholarship_id)

    final_state = run_cv_gen(db, application=application, profile=profile, documents=documents, scholarship=scholarship)

    if final_state.get("blocked"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_gap_detail(final_state.get("gaps") or []))

    return GeneratedDocumentSchema.model_validate(final_state["generated_document"])


@router.post(
    "/applications/{application_id}/generate/sop",
    response_model=GeneratedDocumentSchema,
    responses={409: {"description": "Gap in required information — reported, not filled with invented content"}},
)
def generate_sop(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GeneratedDocumentSchema:
    application = _get_owned_application(db, current_user, application_id)

    profile = get_or_create_profile(db, current_user.id)
    documents = document_repo.list_application_documents_for_application(db, current_user.id, application_id)
    scholarship = scholarship_repo.get_by_id(db, application.scholarship_id)

    final_state = run_sop_gen(db, application=application, profile=profile, documents=documents, scholarship=scholarship)

    if final_state.get("blocked"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_gap_detail(final_state.get("gaps") or []))

    return GeneratedDocumentSchema.model_validate(final_state["generated_document"])
