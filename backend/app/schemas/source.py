"""Source Registry / governance request-response schemas (Blueprint §14, §29).

`domain` is validated as a bare hostname — no scheme, no path — so a
connector (WS2.2) can build a URL from `access_method` + `domain` without
re-parsing an operator-entered value that might smuggle in a scheme or path.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.source import (
    AccessMethod,
    CandidateSourceStatus,
    FetchStatus,
    ReliabilityLevel,
    SourceStatus,
    SourceType,
)

_HOSTNAME_RE = re.compile(
    r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


def _validate_domain(value: str) -> str:
    if "://" in value:
        raise ValueError("domain must not include a URL scheme (e.g. 'https://')")
    if "/" in value:
        raise ValueError("domain must not include a path")
    if not _HOSTNAME_RE.match(value):
        raise ValueError("domain must be a bare hostname (e.g. 'www2.daad.de')")
    return value


class SourceRegistryCreate(BaseModel):
    name: str
    organization: str | None = None
    country: str | None = None
    region: str | None = None
    source_type: SourceType
    official_status: str
    domain: str
    access_method: AccessMethod
    discovery_role: bool = False
    verification_role: bool = False
    reliability_level: ReliabilityLevel
    update_frequency: str | None = None
    status: SourceStatus = SourceStatus.ACTIVE
    extraction_rules: dict = {}
    constraints: dict = {}
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    notes: str | None = None

    @field_validator("domain")
    @classmethod
    def _domain_is_bare_hostname(cls, value: str) -> str:
        return _validate_domain(value)


class SourceRegistryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    organization: str | None = None
    country: str | None = None
    region: str | None = None
    source_type: SourceType
    official_status: str
    domain: str
    access_method: AccessMethod
    discovery_role: bool
    verification_role: bool
    reliability_level: ReliabilityLevel
    update_frequency: str | None = None
    status: SourceStatus
    extraction_rules: dict
    constraints: dict
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    notes: str | None = None


class CandidateSourceCreate(BaseModel):
    discovered_from: str | None = None
    url: str
    proposed_type: SourceType | None = None
    signals: dict = {}


class CandidateSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    discovered_from: str | None = None
    url: str
    proposed_type: SourceType | None = None
    signals: dict
    status: CandidateSourceStatus
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None


class SourceFetchLogCreate(BaseModel):
    source_id: uuid.UUID
    started_at: datetime | None = None
    status: FetchStatus
    http_status: int | None = None
    error: str | None = None
    retry_count: int = 0
    items_found: int | None = None


class SourceFetchLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    started_at: datetime
    status: FetchStatus
    http_status: int | None = None
    error: str | None = None
    retry_count: int
    items_found: int | None = None


class CandidateSourceReject(BaseModel):
    reason: str
