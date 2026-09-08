"""`official_fetch` connector (Blueprint §11, §15) — registry-governed fetch
of official/gov/university source content: (source_id, url) -> normalized
result. MANDATORY gate (constitution Principle IV; PRD B3 Hard-Gated Zone):
this raises `SourceNotApprovedError` before any network call if `source_id`
does not resolve to an `active` `source_registry` row — the LLM cannot
override this by supplying an arbitrary source_id or URL.

No MCP fetch-server provider has been chosen yet (research.md line 55: "the
tool *interface* (`tools/web_fetch.py`) is fixed now regardless of
provider") — this connector reuses that existing httpx-based `fetch_url`
tool as its transport rather than standing up a second HTTP client.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.data.repositories import source_repo
from app.data.repositories.source_repo import SourceNotApprovedError
from app.models.source import FetchStatus
from app.schemas.source import SourceFetchLogCreate
from app.sources.retry_policy import DEFAULT_MAX_ATTEMPTS, run_with_retry
from app.tools.web_fetch import FetchResult, fetch_url

__all__ = ["OfficialFetchResult", "fetch_official", "SourceNotApprovedError"]


@dataclass(frozen=True)
class OfficialFetchResult:
    source_id: uuid.UUID
    source_url: str
    success: bool
    content: str | None = None
    http_status: int | None = None
    fetched_at: datetime | None = None
    error: str | None = None


class _FetchFailed(Exception):
    def __init__(self, fetch_result: FetchResult):
        super().__init__(fetch_result.error)
        self.fetch_result = fetch_result


def fetch_official(
    db: Session,
    source_id: uuid.UUID,
    url: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    fetch: Callable[[str], FetchResult] = fetch_url,
    sleep: Callable[[float], None] | None = None,
) -> OfficialFetchResult:
    source = source_repo.get_source_by_id(db, source_id)
    if source is None:
        raise SourceNotApprovedError(f"source {source_id} is not an active, approved source")

    def _attempt() -> FetchResult:
        result = fetch(url)
        if not result.success:
            raise _FetchFailed(result)
        return result

    retry_kwargs = {"max_attempts": max_attempts}
    if sleep is not None:
        retry_kwargs["sleep"] = sleep
    outcome = run_with_retry(_attempt, **retry_kwargs)

    if outcome.succeeded:
        fetch_result: FetchResult = outcome.result
        source_repo.log_fetch(
            db,
            SourceFetchLogCreate(
                source_id=source_id,
                status=FetchStatus.OK,
                http_status=fetch_result.status_code,
                retry_count=outcome.attempts - 1,
            ),
        )
        return OfficialFetchResult(
            source_id=source_id,
            source_url=url,
            success=True,
            content=fetch_result.html,
            http_status=fetch_result.status_code,
            fetched_at=datetime.now(timezone.utc),
        )

    failed = outcome.last_error
    failure_result = failed.fetch_result if isinstance(failed, _FetchFailed) else None
    error_message = failure_result.error if failure_result else str(failed)
    http_status = failure_result.status_code if failure_result else None

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

    return OfficialFetchResult(
        source_id=source_id,
        source_url=url,
        success=False,
        http_status=http_status,
        error=error_message,
    )
