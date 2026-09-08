"""`api_connector` connector (Blueprint §11) — registry-governed fetch against
an approved scholarship API: (source_id, query) -> normalized records +
provenance. Same governance gate as `official_fetch` (T073): a `source_id`
must resolve to an `active` `source_registry` row before any network call is
attempted (constitution Principle IV; PRD B3 Hard-Gated Zone).

Blueprint §11 lists the impl as "approved scholarship API client" but no
concrete per-provider API has been onboarded yet — `client` is injectable so
each approved API's request/response shaping (driven by that source's
`extraction_rules`) can be wired in later without changing this contract.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy.orm import Session

from app.data.repositories import source_repo
from app.data.repositories.source_repo import SourceNotApprovedError
from app.models.source import FetchStatus, SourceRegistry
from app.schemas.source import SourceFetchLogCreate
from app.sources.retry_policy import DEFAULT_MAX_ATTEMPTS, run_with_retry

__all__ = ["ApiFetchResult", "fetch_api", "SourceNotApprovedError"]

_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class ApiRawResponse:
    success: bool
    status_code: int | None = None
    records: list[dict[str, Any]] | None = None
    error: str | None = None


@dataclass(frozen=True)
class ApiFetchResult:
    source_id: uuid.UUID
    query: str
    success: bool
    records: list[dict[str, Any]]
    http_status: int | None = None
    fetched_at: datetime | None = None
    error: str | None = None


class _ApiCallFailed(Exception):
    def __init__(self, raw: ApiRawResponse):
        super().__init__(raw.error)
        self.raw = raw


def _default_client(source: SourceRegistry, query: str) -> ApiRawResponse:
    """Placeholder transport: `GET https://{domain}?q={query}` expecting a
    JSON body with a top-level `results` list. A real per-source request
    shape belongs to a later slice; this exists only so the connector is
    exercisable without a bespoke client per API."""

    try:
        response = httpx.get(
            f"https://{source.domain}",
            params={"q": query},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        return ApiRawResponse(success=False, error=f"Request timed out: {exc}")
    except httpx.RequestError as exc:
        return ApiRawResponse(success=False, error=f"Could not reach API: {exc}")

    if response.status_code >= 400:
        return ApiRawResponse(
            success=False,
            status_code=response.status_code,
            error=f"API responded with HTTP {response.status_code}",
        )

    body = response.json()
    return ApiRawResponse(success=True, status_code=response.status_code, records=body.get("results", []))


def fetch_api(
    db: Session,
    source_id: uuid.UUID,
    query: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    client: Callable[[SourceRegistry, str], ApiRawResponse] = _default_client,
    sleep: Callable[[float], None] | None = None,
) -> ApiFetchResult:
    source = source_repo.get_source_by_id(db, source_id)
    if source is None:
        raise SourceNotApprovedError(f"source {source_id} is not an active, approved source")

    def _attempt() -> ApiRawResponse:
        raw = client(source, query)
        if not raw.success:
            raise _ApiCallFailed(raw)
        return raw

    retry_kwargs = {"max_attempts": max_attempts}
    if sleep is not None:
        retry_kwargs["sleep"] = sleep
    outcome = run_with_retry(_attempt, **retry_kwargs)

    if outcome.succeeded:
        raw: ApiRawResponse = outcome.result
        source_repo.log_fetch(
            db,
            SourceFetchLogCreate(
                source_id=source_id,
                status=FetchStatus.OK,
                http_status=raw.status_code,
                retry_count=outcome.attempts - 1,
                items_found=len(raw.records or []),
            ),
        )
        return ApiFetchResult(
            source_id=source_id,
            query=query,
            success=True,
            records=raw.records or [],
            http_status=raw.status_code,
            fetched_at=datetime.now(timezone.utc),
        )

    failed = outcome.last_error
    raw_failure = failed.raw if isinstance(failed, _ApiCallFailed) else None
    error_message = raw_failure.error if raw_failure else str(failed)
    http_status = raw_failure.status_code if raw_failure else None

    source_repo.log_fetch(
        db,
        SourceFetchLogCreate(
            source_id=source_id,
            status=FetchStatus.FAIL,
            http_status=http_status,
            error=error_message,
            retry_count=outcome.attempts - 1,
        ),
    )
    source_repo.mark_source_failing(db, source_id)

    return ApiFetchResult(
        source_id=source_id,
        query=query,
        success=False,
        records=[],
        http_status=http_status,
        error=error_message,
    )
