"""Q&A answer schema — stateless structured response, not a persisted table
in MVP (data-model.md §5). Every answer carries a confidence label; an
answer the system cannot verify is Unknown/could-not-confirm rather than a
guess (PRD FR-QA-3)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.common import Confidence


class QaAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer_text: str
    confidence: Confidence
    source_url: str | None = None
    source_id: uuid.UUID | None = None
    last_verified_at: datetime | None = None
    evidence_snippet: str | None = None
