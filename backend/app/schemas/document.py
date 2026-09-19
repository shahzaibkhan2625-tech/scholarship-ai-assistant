"""Document / minimal-Application schemas (T106; contracts/openapi.yaml
`DocumentType`/`UploadedDocument`/`GeneratedDocument`/`Application`;
data-model.md §6-§8). API-layer Pydantic models only — deliberately separate
from the SQLAlchemy models `app.models.document`/`app.models.application`
(slice 3A); `DocumentType` is reused from `app.models.document` rather than
redefined so the response layer and the persisted `application_documents.type`
column never drift out of sync (it is a superset of openapi.yaml's enum by
one value, `unclassified` — the honest default an unparsed upload carries
until T105's classification lands, never a guessed type).

`ParsedDocument` is re-exported from `app.tools.pdf_parse` rather than
redefined here — the tool's `{text, pages, tables}` contract *is* the
API-layer shape, so a second, drift-prone definition would add nothing.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentType, GeneratedDocumentType
from app.tools.pdf_parse import ParsedDocument

__all__ = [
    "DocumentType",
    "ParsedDocument",
    "UploadedDocument",
    "GeneratedDocument",
    "ApplicationCreate",
    "ApplicationRead",
]


class UploadedDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: DocumentType
    parsed_meta: dict | None = None
    satisfies_requirement_id: uuid.UUID | None = None
    inconsistency_flags: list[dict] | None = None


class GeneratedDocument(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: GeneratedDocumentType
    file_ref: str
    source_trace: list[dict] | None = None


class ApplicationCreate(BaseModel):
    scholarship_id: uuid.UUID


class ApplicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scholarship_id: uuid.UUID
    status: str
    created_at: datetime
