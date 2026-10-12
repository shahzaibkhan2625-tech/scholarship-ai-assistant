"""`coverage` service (T084, Blueprint §30.4) — derives a MEASURED coverage
summary from `source_registry` + `source_fetch_log`. Pure read/aggregation:
this module never fetches anything and never writes to either table.

Per §30.4 / PRD B3, coverage is reported as *measured*, never *complete* —
`CoverageSummary.claims_complete_coverage` (schemas/discovery.py) is typed
`Literal[False]`, so nothing this module builds can ever assert full
coverage; a gap is emitted for every (country, source_type) combination that
has no active, currently-non-failing source, even when every configured
source for every OTHER combination is healthy.

`compute_coverage_summary` is the pure function (fixture-testable without a
DB); `get_coverage_summary` is the thin DB-backed wrapper the Discovery Agent
(T089) and `GET /coverage` (002 US4) call.

002 US4 semantics (computed live per request, FR-COVAN-4 — no cache):
- "failed" follows ADR-0008: a source that has monitoring-run history fails when
  its LATEST run had any non-OK row (a check is an OK HTTP row plus possibly a
  FAIL row); a later per-scholarship ingestion row (no run id) does not flip it.
  A source with no run history falls back to its latest raw row (001 rule).
- `last_checked_at` is always the most recent row of any kind.
- `failing` status counts as active-but-unhealthy: it is in `sources_active`,
  never counts as healthy coverage, and gets its own gap reason.
- `sources_checked`/`sources_failed` keep the 001 rule (any status, so disabled
  sources with a log are included).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.data.repositories import scholarship_repo, source_repo
from app.models.source import FetchStatus, SourceStatus
from app.schemas.discovery import CoverageSummary

__all__ = ["SourceSnapshot", "FetchLogSnapshot", "compute_coverage_summary", "get_coverage_summary"]


@dataclass(frozen=True)
class SourceSnapshot:
    """Minimal, DB-agnostic view of one `source_registry` row — lets
    `compute_coverage_summary` be unit-tested with plain fixtures."""

    id: uuid.UUID
    country: str | None
    source_type: str
    status: str  # SourceStatus value


@dataclass(frozen=True)
class FetchLogSnapshot:
    """Minimal, DB-agnostic view of one `source_fetch_log` row."""

    source_id: uuid.UUID
    status: str  # FetchStatus value
    started_at: datetime
    monitoring_run_id: uuid.UUID | None = None


_FAILING_STATUSES = {FetchStatus.FAIL.value, FetchStatus.TIMEOUT.value}
_ACTIVE_STATUSES = {SourceStatus.ACTIVE.value, SourceStatus.FAILING.value}


def _latest_outcome_failed(logs: list[FetchLogSnapshot]) -> bool:
    """`logs` is one source's rows, oldest first (non-empty). Latest monitoring RUN's
    outcome when the source has run history, else the latest raw row."""
    run_rows = [entry for entry in logs if entry.monitoring_run_id is not None]
    if not run_rows:
        return logs[-1].status in _FAILING_STATUSES
    latest_run_id = run_rows[-1].monitoring_run_id
    return any(
        entry.status in _FAILING_STATUSES for entry in run_rows if entry.monitoring_run_id == latest_run_id
    )


def compute_coverage_summary(
    sources: list[SourceSnapshot],
    fetch_logs: list[FetchLogSnapshot],
    *,
    now: datetime | None = None,
    candidate_sources_pending: int = 0,
    verified_opportunities_count: int = 0,
) -> CoverageSummary:
    now = now or datetime.now(timezone.utc)

    # `failing` is approved-but-degraded (ADR-0008): counted active, never healthy.
    active_sources = [s for s in sources if s.status in _ACTIVE_STATUSES]

    logs_by_source: dict[uuid.UUID, list[FetchLogSnapshot]] = {}
    for log in fetch_logs:
        logs_by_source.setdefault(log.source_id, []).append(log)

    sources_checked = 0
    sources_failed = 0
    last_checked_at: dict[str, datetime] = {}
    currently_failing_ids: set[uuid.UUID] = set()

    for source in sources:
        logs = sorted(logs_by_source.get(source.id, []), key=lambda entry: entry.started_at)
        if not logs:
            continue
        sources_checked += 1
        latest = logs[-1]
        last_checked_at[str(source.id)] = latest.started_at
        if _latest_outcome_failed(logs):
            sources_failed += 1
            currently_failing_ids.add(source.id)

    # Every (country, source_type) combination configured anywhere in the
    # registry — a gap is any combo with zero active, currently-healthy
    # sources, whether that's because none was ever configured active or
    # because every active one for it is currently failing.
    combos = sorted({(s.country, s.source_type) for s in sources}, key=lambda c: (c[0] or "", c[1]))
    gaps: list[str] = []
    countries_covered: set[str] = set()

    for country, source_type in combos:
        combo_active = [s for s in active_sources if s.country == country and s.source_type == source_type]
        healthy = [
            s
            for s in combo_active
            if s.id not in currently_failing_ids and s.status != SourceStatus.FAILING.value
        ]
        label = f"{country or 'unspecified country'} / {source_type}"
        if not healthy:
            reason = "all active sources currently failing" if combo_active else "no active source configured"
            gaps.append(f"{label}: {reason}")
        elif country:
            countries_covered.add(country)

    return CoverageSummary(
        as_of=now,
        sources_configured=len(sources),
        sources_active=len(active_sources),
        sources_checked=sources_checked,
        sources_failed=sources_failed,
        candidate_sources_pending=candidate_sources_pending,
        verified_opportunities_count=verified_opportunities_count,
        last_checked_at=last_checked_at,
        countries_covered=sorted(countries_covered),
        gaps=gaps,
    )


def get_coverage_summary(db: Session) -> CoverageSummary:
    """DB-backed wrapper: reads every configured source (any status, via
    `source_repo.get_all_sources` — NOT the active-only gate, since coverage
    reporting needs to see disabled/failing rows too), plus a BOUNDED slice of the
    fetch log (each source's latest row and its latest monitoring run's rows —
    never the whole history), then reduces them with `compute_coverage_summary`.
    Read-only; a handful of sequential queries, no per-source loop."""

    def _enum_value(value):
        return value.value if hasattr(value, "value") else value

    sources = [
        SourceSnapshot(
            id=source.id,
            country=source.country,
            source_type=_enum_value(source.source_type),
            status=_enum_value(source.status),
        )
        for source in source_repo.get_all_sources(db)
    ]
    rows = {
        log.id: log
        for log in [*source_repo.get_latest_fetch_log_per_source(db), *source_repo.get_latest_run_fetch_logs(db)]
    }
    logs = [
        FetchLogSnapshot(
            source_id=log.source_id,
            status=_enum_value(log.status),
            started_at=log.started_at,
            monitoring_run_id=log.monitoring_run_id,
        )
        for log in rows.values()
    ]
    return compute_coverage_summary(
        sources,
        logs,
        candidate_sources_pending=source_repo.count_pending_candidate_sources(db),
        verified_opportunities_count=scholarship_repo.count_verified_opportunities(db),
    )
