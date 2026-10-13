"""Candidate-source validation response schema (002 US7, contracts/openapi.yaml
`CandidateSourceValidation`). Advisory only: nothing here gates approval."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CandidateSourceValidationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_source_id: uuid.UUID
    checked_at: datetime
    reachable: bool
    reachable_detail: str | None = None
    extractable: bool
    extractable_detail: str | None = None
    official_signals: dict
