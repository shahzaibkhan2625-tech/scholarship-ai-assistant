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

**`fetch_and_extract_listing` (T135) lives here, not in the Discovery Agent,
on purpose:** `tests/agents/test_discovery_guardrails.py` statically asserts
the agent module may only call `source_repo.get_active_sources`/
`get_source_by_id` — never a write/log method — so that no `source_id`
gating, failure-logging, or `failing`-status transition can be scattered
across agent logic outside this connector tier (PRD B3 no-self-
authorization). Exactly like `fetch_official` above already does for a raw
fetch, `fetch_and_extract_listing` owns its OWN `source_repo.log_fetch`/
`mark_source_failing` calls for the extraction step, and the Discovery Agent
only ever sees the already-shaped `listing_fetch_tool` output.

**New-source detection (US4 Acceptance Scenario 3, ADR-0005) lives here for
the exact same reason:** the same guardrail test forbids `create_candidate_source`
from being called inside the agent module, so `_detect_and_record_candidate_sources`
below — the only caller of `source_repo.record_or_bump_candidate_source` in
this codebase — runs here too, over this call's raw fetched HTML, right
after a successful `fetch_official` and before `extract_listing` tag-strips
it (link `href` attributes don't survive that stripping). It is wrapped so a
detection failure can never affect the listing-extraction result it rides
alongside, and a miss is never logged as a coverage gap — this is a
best-effort signal, not a completeness guarantee (ADR-0005 Decision 4).
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, NamedTuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.data.repositories import source_repo
from app.data.repositories.source_repo import SourceNotApprovedError
from app.models.source import FetchStatus, VerificationStatus
from app.schemas.source import SourceFetchLogCreate
from app.sources.retry_policy import DEFAULT_MAX_ATTEMPTS, run_with_retry
from app.tools.detect_candidate_links import find_candidate_links
from app.tools.extract_listing import ExtractListingOutput, extract_listing_from_page
from app.tools.web_fetch import FetchResult, fetch_url

logger = logging.getLogger(__name__)

__all__ = [
    "FetchTarget",
    "resolve_fetch_target",
    "OfficialFetchResult",
    "fetch_official",
    "CachedScholarshipRef",
    "ListingExtractionResult",
    "fetch_and_extract_listing",
    "SourceNotApprovedError",
]


class FetchTarget(NamedTuple):
    url: str
    used_homepage_fallback: bool


def _valid_listing_candidate(candidate: object, domain: str) -> bool:
    """Same rule as `SourceRegistryCreate._listing_url_matches_domain`: http(s)
    and hostname equal to the source domain (case-insensitive). Kept as a
    separate helper (the schema validator is deliberately not refactored); a
    parity test keeps the two in step."""
    if not isinstance(candidate, str) or not candidate:
        return False
    parsed = urlparse(candidate)
    return parsed.scheme.lower() in ("http", "https") and (parsed.hostname or "").lower() == domain.lower()


def resolve_fetch_target(source) -> FetchTarget:
    """Pure (no DB, no I/O, no caching): which URL to fetch for `source`.
    Precedence: `listing_page_url` column -> legacy
    `extraction_rules["list_page_url"]` -> `https://{domain}` (the only branch
    with `used_homepage_fallback=True`). An invalid candidate is skipped with a
    warning and the next tier tried; this never raises."""

    legacy = (source.extraction_rules or {}).get("list_page_url")
    for candidate in (source.listing_page_url, legacy):
        if candidate is None:
            continue
        if _valid_listing_candidate(candidate, source.domain):
            return FetchTarget(candidate, False)
        logger.warning("ignoring invalid listing URL %r for source domain %s", candidate, source.domain)
    return FetchTarget(f"https://{source.domain}", True)


def _bare_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is_homepage(url: str) -> bool:
    return urlparse(url).path.rstrip("/") == ""


@dataclass(frozen=True)
class OfficialFetchResult:
    source_id: uuid.UUID
    source_url: str
    success: bool
    content: str | None = None
    http_status: int | None = None
    fetched_at: datetime | None = None
    error: str | None = None
    final_url: str | None = None


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
    used_homepage_fallback: bool = False,
    monitoring_run_id: uuid.UUID | None = None,
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
                fetched_url=url,
                used_homepage_fallback=used_homepage_fallback,
                monitoring_run_id=monitoring_run_id,
            ),
        )
        return OfficialFetchResult(
            source_id=source_id,
            source_url=url,
            success=True,
            content=fetch_result.html,
            http_status=fetch_result.status_code,
            fetched_at=datetime.now(timezone.utc),
            final_url=fetch_result.final_url,
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
            fetched_url=url,
            used_homepage_fallback=used_homepage_fallback,
            monitoring_run_id=monitoring_run_id,
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


# T135/A9: re-extracting a listing page more often than the source itself
# claims to change is pure LLM-quota waste, so the staleness window is keyed
# off `SourceRegistry.update_frequency` (already seeded per source, per
# seed_sources.yaml) rather than one new global magic constant. Anything
# unrecognized/missing falls back to a conservative 24h.
_UPDATE_FREQUENCY_TTL: dict[str, timedelta] = {
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
    "annual": timedelta(days=365),
}
_DEFAULT_EXTRACTION_TTL = timedelta(hours=24)


def _extraction_ttl(update_frequency: str | None) -> timedelta:
    return _UPDATE_FREQUENCY_TTL.get(update_frequency or "", _DEFAULT_EXTRACTION_TTL)


def _last_successful_extraction(db: Session, source_id: uuid.UUID):
    """The most recent `source_fetch_log` row that represents a real past
    LISTING-EXTRACTION success (status OK *and* `items_found` populated) for
    this source — not just a raw HTTP-200, which `fetch_official` above logs
    on every attempt regardless of whether extraction ever ran.
    `get_fetch_logs_for_source` is already ordered `started_at desc`."""
    for log in source_repo.get_fetch_logs_for_source(db, source_id):
        if log.status == FetchStatus.OK and log.items_found is not None:
            return log
    return None


@dataclass(frozen=True)
class CachedScholarshipRef:
    scholarship_id: uuid.UUID
    verification_status: VerificationStatus


@dataclass(frozen=True)
class ListingExtractionResult:
    source_id: uuid.UUID
    status: str  # "ok" | "fail" | "cached"
    candidates: list[dict] = field(default_factory=list)  # ready for run_ingestion; "ok" only
    cached: list[CachedScholarshipRef] = field(default_factory=list)  # already-persisted refs; "cached" only
    error: str | None = None
    fetched_url: str | None = None
    used_homepage_fallback: bool = False


def _detect_and_record_candidate_sources(db: Session, url: str, html: str) -> None:
    """US4 Acceptance Scenario 3 / ADR-0005: best-effort new-source detection
    over this fetch's raw HTML (module docstring above explains why this
    lives here). `known_domains` is built from the same active-only-gated
    read the rest of this tier already uses (`get_active_sources`) — a
    read, never a fetch gate; `record_or_bump_candidate_source` still
    independently checks ANY status before writing, so a pending/disabled
    domain this read doesn't surface is still never re-proposed.

    Swallows every exception: candidate detection must never affect the
    listing-extraction result it rides alongside, and a failure here is not
    a coverage gap — it's simply a signal that didn't fire this time."""

    try:
        known_domains = {source.domain for source in source_repo.get_active_sources(db)}
        for signal in find_candidate_links(html, source_url=url, known_domains=known_domains):
            source_repo.record_or_bump_candidate_source(
                db,
                domain=signal.domain,
                url=signal.url,
                discovered_from=url,
                matched_keyword=signal.matched_keyword,
            )
    except Exception:  # noqa: BLE001 - detection is best-effort, never allowed to break extraction
        pass


def fetch_and_extract_listing(
    db: Session,
    source_id: uuid.UUID,
    url: str | None = None,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    fetch: Callable[[str], FetchResult] = fetch_url,
    extract_listing: Callable[..., ExtractListingOutput] = extract_listing_from_page,
    sleep: Callable[[float], None] | None = None,
    monitoring_run_id: uuid.UUID | None = None,
) -> ListingExtractionResult:
    """T135: fetch (`fetch_official` above) -> `extract_listing` -> shaped
    result, gated by a two-layer cache so a source isn't re-fetched/
    re-extracted more often than its own `update_frequency` claims it
    changes (T135/A9). All `source_repo` reads/writes for the extraction
    step live here, in the connector tier — never in the Discovery Agent
    (see module docstring).

    `url=None` (002) resolves the target via `resolve_fetch_target`; an
    explicit `url` behaves exactly as in 001 (`(url, False)`, never resolved,
    never redirect-checked)."""

    source = source_repo.get_source_by_id(db, source_id)
    if source is None:
        raise SourceNotApprovedError(f"source {source_id} is not an active, approved source")

    resolved = url is None
    if resolved:
        target = resolve_fetch_target(source)
        url, used_homepage_fallback = target.url, target.used_homepage_fallback
    else:
        used_homepage_fallback = False
    log_context = dict(
        fetched_url=url, used_homepage_fallback=used_homepage_fallback, monitoring_run_id=monitoring_run_id
    )

    last_extraction = _last_successful_extraction(db, source_id)
    # On the resolved path a cached row only counts if it was fetched from this
    # very target (NULL pre-002 rows never match), so changing/clearing the
    # listing URL is never answered from a different page's extraction.
    if last_extraction is not None and resolved and last_extraction.fetched_url != url:
        last_extraction = None
    if last_extraction is not None:
        age = datetime.now(timezone.utc) - last_extraction.started_at
        if age < _extraction_ttl(source.update_frequency):
            cached = [
                CachedScholarshipRef(scholarship_id=ss.scholarship_id, verification_status=ss.verification_status)
                for ss in source_repo.get_scholarship_sources_by_source(db, source_id)
            ]
            return ListingExtractionResult(
                source_id=source_id,
                status="cached",
                cached=cached,
                fetched_url=url,
                used_homepage_fallback=used_homepage_fallback,
            )

    fetch_result = fetch_official(
        db,
        source_id,
        url,
        max_attempts=max_attempts,
        fetch=fetch,
        sleep=sleep,
        used_homepage_fallback=used_homepage_fallback,
        monitoring_run_id=monitoring_run_id,
    )
    if not fetch_result.success:
        return ListingExtractionResult(
            source_id=source_id,
            status="fail",
            error=fetch_result.error,
            fetched_url=url,
            used_homepage_fallback=used_homepage_fallback,
        )

    if resolved and fetch_result.final_url:
        redirect_error = None
        if _bare_host(fetch_result.final_url) != _bare_host(f"https://{source.domain}"):
            redirect_error = f"off-domain redirect: {fetch_result.final_url}"
        elif _is_homepage(fetch_result.final_url) and not _is_homepage(url):
            redirect_error = f"redirected to homepage: {url}"
        if redirect_error is not None:
            source_repo.log_fetch(
                db,
                SourceFetchLogCreate(
                    source_id=source_id, status=FetchStatus.FAIL, error=redirect_error, retry_count=0, **log_context
                ),
            )
            return ListingExtractionResult(
                source_id=source_id,
                status="fail",
                error=redirect_error,
                fetched_url=url,
                used_homepage_fallback=used_homepage_fallback,
            )

    _detect_and_record_candidate_sources(db, url, fetch_result.content or "")

    extraction = extract_listing(fetch_result.content or "", source_url=url)

    if extraction.accepted_count == 0:
        # T135/A5+A8: a successful fetch yielding zero GROUNDED candidates
        # (empty listing, changed page structure, parse failure, or only
        # hallucinated names) must not be silently indistinguishable from "no
        # scholarships found" -- logged and the source marked failing,
        # exactly like a real fetch failure, so it surfaces as a coverage gap.
        detail = "listing extraction yielded 0 candidates"
        if extraction.rejected_count:
            detail = f"listing extraction yielded 0 grounded candidates (rejected {extraction.rejected_count} ungrounded)"
        source_repo.log_fetch(
            db,
            SourceFetchLogCreate(
                source_id=source_id, status=FetchStatus.FAIL, error=detail, retry_count=0, **log_context
            ),
        )
        source_repo.mark_source_failing(db, source_id)
        return ListingExtractionResult(
            source_id=source_id,
            status="fail",
            error=detail,
            fetched_url=url,
            used_homepage_fallback=used_homepage_fallback,
        )

    # Some candidates were hallucinated and rejected by grounding but others
    # were accepted: proceed with the good ones, but the rejection is still
    # logged (not silently dropped) via the OK row's `error` note.
    log_note = (
        f"rejected {extraction.rejected_count} ungrounded candidate(s): {extraction.rejected_names[:5]}"
        if extraction.rejected_count
        else None
    )
    source_repo.log_fetch(
        db,
        SourceFetchLogCreate(
            source_id=source_id,
            status=FetchStatus.OK,
            retry_count=0,
            items_found=extraction.accepted_count,
            error=log_note,
            **log_context,
        ),
    )

    raw_candidates = [candidate.model_dump(exclude_none=True) for candidate in extraction.candidates]
    return ListingExtractionResult(
        source_id=source_id,
        status="ok",
        candidates=raw_candidates,
        fetched_url=url,
        used_homepage_fallback=used_homepage_fallback,
    )
