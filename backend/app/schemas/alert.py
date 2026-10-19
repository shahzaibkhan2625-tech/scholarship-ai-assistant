"""Alert request/response schemas (002, contracts/openapi.yaml `Alert*`)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scholarship_id: uuid.UUID
    match_id: uuid.UUID | None = None
    change_summary: dict
    # `list[str]`, not a Literal of the two shipped channels: a future channel
    # registered in services/alerts.py must serialize without a schema change.
    channels_requested: list[str]
    delivered_in_app_at: datetime | None = None
    delivered_email_at: datetime | None = None
    email_error: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class AlertPreferencesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    in_app_enabled: bool
    email_enabled: bool


class AlertPreferencesUpdate(BaseModel):
    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
