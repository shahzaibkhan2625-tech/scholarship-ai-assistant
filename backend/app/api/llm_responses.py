"""The documented 503 of the LLM-backed student routes (003, contracts/api-delta.md section 1).

The body is produced by the exception handler in `app/main.py`; this model only documents it so the
live OpenAPI and the 003 contract agree. `POST /applications/{id}/documents` does not use it: that route
keeps its 201 with an `extraction_unavailable` flag.
"""

from datetime import datetime

from pydantic import BaseModel


class LlmUnavailable(BaseModel):
    detail: str
    code: str
    retry_after_seconds: int
    retry_at: datetime
    reason: str


LLM_UNAVAILABLE_RESPONSES = {
    503: {
        "model": LlmUnavailable,
        "description": "AI features are temporarily unavailable; see the Retry-After header.",
    }
}
