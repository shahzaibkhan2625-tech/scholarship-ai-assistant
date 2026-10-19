"""Scholarship read schema (002 US5, FR-LIFECYCLE-1, contracts `ScholarshipLifecycleView`).

Deliberately minimal: it exists so a record's single lifecycle state is observable
over HTTP. Both state fields are the enums themselves, so a response can only ever
carry one value from the fixed set."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.scholarship import LifecycleStatus, VerificationStatus


class ScholarshipLifecycleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    lifecycle_status: LifecycleStatus
    verification_status: VerificationStatus
    last_verified_at: datetime | None = None
    deadline: date | None = None
