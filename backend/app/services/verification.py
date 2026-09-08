"""`verification` service (T082, Blueprint §32) — computes `lifecycle_status`
from `retrieved_at`, `deadline`, and whether an official source confirmed the
record. Per §32 ("the official source is the final authority"), a record
that has never been confirmed by an official source is never assigned
`verified` — the strongest it can reach is `unverified` (once previously
seen) or `newly_discovered` (first sighting), regardless of how many
third-party sources agree.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.models.scholarship import LifecycleStatus

__all__ = ["VerificationInput", "compute_lifecycle_status"]

_STALE_AFTER_DAYS = 180


@dataclass(frozen=True)
class VerificationInput:
    retrieved_at: datetime
    last_verified_at: datetime | None
    deadline: date | None
    official_source_confirmed: bool
    closing_status: str | None = None
    previously_closed: bool = False
    source_unavailable: bool = False
    now: datetime | None = None


def compute_lifecycle_status(data: VerificationInput) -> LifecycleStatus:
    now = data.now or datetime.now(timezone.utc)

    if data.source_unavailable:
        return LifecycleStatus.SOURCE_UNAVAILABLE

    if data.closing_status == "closed":
        return LifecycleStatus.CLOSED

    if data.deadline is not None and data.deadline < now.date():
        return LifecycleStatus.EXPIRED

    if not data.official_source_confirmed:
        # Never 'verified' without an official-source confirmation (§32).
        if data.last_verified_at is None:
            return LifecycleStatus.NEWLY_DISCOVERED
        return LifecycleStatus.UNVERIFIED

    if data.previously_closed:
        return LifecycleStatus.REOPENED

    if data.last_verified_at is None:
        return LifecycleStatus.NEWLY_DISCOVERED

    age_days = (now - data.last_verified_at).days
    if age_days > _STALE_AFTER_DAYS:
        return LifecycleStatus.STALE

    if data.retrieved_at > data.last_verified_at:
        return LifecycleStatus.UPDATED

    return LifecycleStatus.VERIFIED
