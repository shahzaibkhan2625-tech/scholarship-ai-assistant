"""Documents API (T108; contracts/openapi.yaml `POST
/applications/{id}/documents`). `user_id` is always loaded server-side from
the authenticated token, mirroring `api/application.py`'s pattern.

Ordering is load-bearing (Edge Cases: "a document upload fails to parse ->
the user is told parsing failed and asked to retry/replace, not silently
ignored"; data-model.md §6 "never a row left in an ambiguous state"):
1. Verify the application belongs to the authenticated user (404 otherwise).
2. Persist the file to storage, then persist the `application_documents` row
   (`type=unclassified`, `parsed_meta=NULL`) — durably, BEFORE parsing, so a
   pipeline failure can never lose the user's upload.
3. Only then invoke `doc_pipeline`. A genuine parse failure (corrupt,
   truncated, encrypted, or unsupported file) maps to `422`; the row from
   step 2 still exists so the upload is retryable and inspectable. Anything
   else (including a successfully-parsed-but-empty scanned document) is a
   normal `201`.
"""

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.data.files.storage import get_storage
from app.data.repositories import application_repo, document_repo, scholarship_repo
from app.data.repositories.db import get_db
from app.models.document import DocumentType
from app.models.user import User
from app.schemas.document import UploadedDocument
from app.services.profile import get_or_create_profile
from app.workflows.doc_pipeline.graph import run_doc_pipeline

router = APIRouter()


@router.post(
    "/applications/{application_id}/documents",
    response_model=UploadedDocument,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    application_id: uuid.UUID,
    file: UploadFile = File(...),
    document_type: DocumentType = Form(..., alias="type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UploadedDocument:
    application = application_repo.get_by_id_for_user(db, current_user.id, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Application not found")

    file_bytes = file.file.read()
    storage = get_storage()
    file_ref = storage.save(current_user.id, file.filename or "upload", file_bytes)
    checksum = hashlib.sha256(file_bytes).hexdigest()

    document = document_repo.create_application_document(db, current_user.id, application_id, file_ref, checksum)

    scholarship = scholarship_repo.get_by_id(db, application.scholarship_id)
    profile = get_or_create_profile(db, current_user.id)

    final_state = run_doc_pipeline(
        db,
        document=document,
        file_bytes=file_bytes,
        filename=file.filename or "",
        declared_type=document_type.value,
        profile=profile,
        scholarship=scholarship,
    )

    if final_state.get("error"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="We could not parse this file. Please retry the upload or supply a different file.",
        )

    return UploadedDocument.model_validate(document)
