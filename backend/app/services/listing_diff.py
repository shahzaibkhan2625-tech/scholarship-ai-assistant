"""`listing_diff` service (002 US1, T179) — pure, deterministic comparison of a
stored scholarship against a freshly extracted listing candidate.

No DB, no clock, no LLM. Only fields the listing extractor actually emits and
that map to a first-class `Scholarship` column are monitored:
`deadline`, `funding_status`, `degree_level`.

**Known spec gap (FR-MON-3):** "requirement" changes cannot be detected here
(or anywhere in monitoring) because the listing extractor never emits
requirements data — only the three fields above. FR-MON-3 is therefore only
partially covered; this is a real gap, not a claimed full coverage.

A field the candidate does not state is never a change and is never written:
an absent/unparseable value, or `funding_status == "unknown"` (the extractor's
default when the page says nothing), means "not stated", so stored knowledge
is never degraded to unknown by a listing that is simply silent.
"""

from datetime import date
from typing import Any, Mapping, Protocol

from app.models.scholarship import DegreeLevel, FundingStatus

__all__ = ["MONITORED_FIELDS", "diff_listing_fields"]

MONITORED_FIELDS = ("deadline", "funding_status", "degree_level")


class _StoredLike(Protocol):
    deadline: date | None
    funding_status: FundingStatus
    degree_level: DegreeLevel | None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _coerce_enum(enum_cls, value):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


def extract_monitored_values(raw: Mapping[str, Any]) -> dict[str, Any]:
    """The typed, stated-only values `raw` carries for the monitored fields."""
    values: dict[str, Any] = {
        "deadline": _parse_date(raw.get("deadline")),
        "degree_level": _coerce_enum(DegreeLevel, raw.get("degree_level")),
        "funding_status": _coerce_enum(FundingStatus, raw.get("funding_status")),
    }
    if values["funding_status"] == FundingStatus.UNKNOWN:
        values["funding_status"] = None
    return {key: value for key, value in values.items() if value is not None}


def diff_listing_fields(stored: _StoredLike, raw: Mapping[str, Any]) -> dict[str, Any]:
    """`{field: new_value}` for every monitored field the candidate states and
    whose value differs from `stored`. Empty dict means "unchanged"."""
    return {
        key: value
        for key, value in extract_monitored_values(raw).items()
        if getattr(stored, key) != value
    }
