"""Document data access — CRUD for `application_documents` and
`generated_documents`. Every query is `user_id`-scoped (FR-AUTH-2)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import ApplicationDocument, DocumentType, GeneratedDocument, GeneratedDocumentType


def create_application_document(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    file_ref: str,
    checksum: str,
) -> ApplicationDocument:
    document = ApplicationDocument(
        user_id=user_id,
        application_id=application_id,
        file_ref=file_ref,
        checksum=checksum,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def get_application_document_by_id(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID
) -> ApplicationDocument | None:
    stmt = select(ApplicationDocument).where(
        ApplicationDocument.id == document_id, ApplicationDocument.user_id == user_id
    )
    return db.execute(stmt).scalar_one_or_none()


def list_application_documents_for_application(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> list[ApplicationDocument]:
    stmt = select(ApplicationDocument).where(
        ApplicationDocument.user_id == user_id,
        ApplicationDocument.application_id == application_id,
    )
    return list(db.execute(stmt).scalars().all())


def update_application_document(
    db: Session,
    user_id: uuid.UUID,
    document: ApplicationDocument,
    *,
    type: DocumentType | None = None,
    parsed_meta: dict | None = None,
    satisfies_requirement_id: uuid.UUID | None = None,
    inconsistency_flags: list | None = None,
) -> ApplicationDocument:
    if document.user_id != user_id:
        raise PermissionError(f"document {document.id} does not belong to user {user_id}")

    if type is not None:
        document.type = type
    if parsed_meta is not None:
        document.parsed_meta = parsed_meta
    if satisfies_requirement_id is not None:
        document.satisfies_requirement_id = satisfies_requirement_id
    if inconsistency_flags is not None:
        document.inconsistency_flags = inconsistency_flags
    db.commit()
    db.refresh(document)
    return document


def delete_application_document(db: Session, user_id: uuid.UUID, document: ApplicationDocument) -> None:
    if document.user_id != user_id:
        raise PermissionError(f"document {document.id} does not belong to user {user_id}")

    db.delete(document)
    db.commit()


def create_generated_document(
    db: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    type: GeneratedDocumentType,
    file_ref: str,
) -> GeneratedDocument:
    document = GeneratedDocument(
        user_id=user_id,
        application_id=application_id,
        type=type,
        file_ref=file_ref,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def get_generated_document_by_id(
    db: Session, user_id: uuid.UUID, document_id: uuid.UUID
) -> GeneratedDocument | None:
    stmt = select(GeneratedDocument).where(
        GeneratedDocument.id == document_id, GeneratedDocument.user_id == user_id
    )
    return db.execute(stmt).scalar_one_or_none()


def list_generated_documents_for_application(
    db: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> list[GeneratedDocument]:
    stmt = select(GeneratedDocument).where(
        GeneratedDocument.user_id == user_id,
        GeneratedDocument.application_id == application_id,
    )
    return list(db.execute(stmt).scalars().all())
