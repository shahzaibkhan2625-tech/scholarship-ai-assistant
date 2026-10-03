"""`lifecycle` service (T158, FR-LIFECYCLE-1..5) — monitoring-driven
`lifecycle_status` transitions.

Pure and deterministic: no DB, no clock (`now` is passed in), no LLM
(FR-LIFECYCLE-5, constitution Principle II). Both evaluators return the NEW
`LifecycleStatus`, or `None` when nothing should change, so callers never
write a spurious transition (US1 Scenario 5). Complements — does not replace —
`services/verification.py::compute_lifecycle_status`.

Never returns UPDATED or NEWLY_DISCOVERED: those come from the US1 diff step
(T179) and ingestion respectively, not from this service.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from app.models.scholarship import LifecycleStatus, VerificationStatus
from app.services.verification import _STALE_AFTER_DAYS

__all__ = [
    "DEFAULT_FRESHNESS_WINDOW_DAYS",
    "FetchOutcome",
    "ListedState",
    "evaluate_fetch_outcome",
    "evaluate_freshness",
]

# Service-level default used when `source_registry.freshness_window_days` is NULL.
# Deliberately the same number verification.py already uses for "stale".
DEFAULT_FRESHNESS_WINDOW_DAYS = _STALE_AFTER_DAYS

LS = LifecycleStatus

_STALE_ELIGIBLE = frozenset({LS.VERIFIED, LS.UNVERIFIED, LS.UPDATED, LS.REOPENED})
_FAILED_FETCH_PRESERVES = frozenset({LS.SOURCE_UNAVAILABLE, LS.CLOSED, LS.EXPIRED})
_RECOVERING = frozenset({LS.STALE, LS.SOURCE_UNAVAILABLE})


class ListedState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    ABSENT = "absent"


class _ScholarshipLike(Protocol):
    lifecycle_status: LifecycleStatus
    verification_status: VerificationStatus
    last_verified_at: datetime | None


class _SourceLike(Protocol):
    freshness_window_days: int | None


@dataclass(frozen=True)
class FetchOutcome:
    """What one monitoring fetch learned about one scholarship record.

    `listed_state` None means "not evaluable for this record".
    """

    fetch_succeeded: bool
    retries_exhausted: bool = False
    extraction_grounded: bool = False
    listed_state: ListedState | None = None


def evaluate_freshness(
    scholarship: _ScholarshipLike, source: _SourceLike, now: datetime
) -> LifecycleStatus | None:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if scholarship.lifecycle_status not in _STALE_ELIGIBLE:
        return None
    if scholarship.last_verified_at is None:
        return None
    window_days = (
        source.freshness_window_days
        if source.freshness_window_days is not None
        else DEFAULT_FRESHNESS_WINDOW_DAYS
    )
    if now - scholarship.last_verified_at > timedelta(days=window_days):
        return LS.STALE
    return None


def _decide(scholarship: _ScholarshipLike, outcome: FetchOutcome) -> LifecycleStatus | None:
    current = scholarship.lifecycle_status

    if not outcome.fetch_succeeded:
        # 1) exhausted retries -> SOURCE_UNAVAILABLE; 2) otherwise no change.
        # A failed fetch never yields EXPIRED/CLOSED and never overwrites them.
        if outcome.retries_exhausted and current not in _FAILED_FETCH_PRESERVES:
            return LS.SOURCE_UNAVAILABLE
        return None

    # 3) an empty or failed extraction never closes or exits anything.
    if not outcome.extraction_grounded:
        return None

    listed = outcome.listed_state
    if listed in (ListedState.CLOSED, ListedState.ABSENT):  # 4)
        return LS.CLOSED if current not in (LS.CLOSED, LS.EXPIRED) else None
    if listed == ListedState.OPEN:
        if current == LS.CLOSED:  # 5)
            return LS.REOPENED
        confirmed = scholarship.verification_status == VerificationStatus.VERIFIED
        if current in _RECOVERING:  # 6) O-3
            return LS.VERIFIED if confirmed else LS.UNVERIFIED
        if current == LS.UNVERIFIED and confirmed:  # 7)
            return LS.VERIFIED
    return None  # 8)


def evaluate_fetch_outcome(scholarship: _ScholarshipLike, outcome: FetchOutcome) -> LifecycleStatus | None:
    target = _decide(scholarship, outcome)
    return None if target == scholarship.lifecycle_status else target
