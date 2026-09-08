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
(T089) calls.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.data.repositories import source_repo
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


_FAILING_STATUSES = {FetchStatus.FAIL.value, FetchStatus.TIMEOUT.value}


def compute_coverage_summary(
    sources: list[SourceSnapshot],
    fetch_logs: list[FetchLogSnapshot],
    *,
    now: datetime | None = None,
) -> CoverageSummary:
    now = now or datetime.now(timezone.utc)

    active_sources = [s for s in sources if s.status == SourceStatus.ACTIVE.value]

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
        if latest.status in _FAILING_STATUSES:
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
        healthy = [s for s in combo_active if s.id not in currently_failing_ids]
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
        last_checked_at=last_checked_at,
        countries_covered=sorted(countries_covered),
        gaps=gaps,
    )


def get_coverage_summary(db: Session) -> CoverageSummary:
    """DB-backed wrapper: reads every configured source (any status, via
    `source_repo.get_all_sources` — NOT the active-only gate, since coverage
    reporting needs to see disabled/failing rows too) and every fetch log,
    then reduces them with `compute_coverage_summary`. Read-only."""

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
    logs = [
        FetchLogSnapshot(
            source_id=log.source_id,
            status=_enum_value(log.status),
            started_at=log.started_at,
        )
        for log in source_repo.get_all_fetch_logs(db)
    ]
    return compute_coverage_summary(sources, logs)
