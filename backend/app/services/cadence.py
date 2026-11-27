"""Per-source check cadence (003 US3, FR-CADENCE-1..5, research R-09). Pure functions: no database, no clock.

A source's check interval comes from its operator override (`extraction_rules['check_interval_minutes']`),
else from its declared `update_frequency`, else from the conservative daily default; it never exceeds half
the source's freshness window (the same half the forced full re-check uses), and a shortened interval is
reported so the caller can flag it. A source is due at `max(next_attempt_at, last_checked_at + interval)`;
the stalest due source is checked first.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Mapping

from app.core.config import settings
from app.services.lifecycle import DEFAULT_FRESHNESS_WINDOW_DAYS

__all__ = ["Interval", "MIN_INTERVAL_MINUTES", "backoff_minutes", "check_interval", "is_due", "next_due_at", "select_due"]

MIN_INTERVAL_MINUTES = 15
_MINUTES_PER_DAY = 1440
_MAX_BACKOFF_EXPONENT = 20  # 2**20 * base is far above any interval; avoids huge integers on a runaway level

_SETTING_BY_FREQUENCY = {
    "weekly": "cadence_weekly_minutes",
    "monthly": "cadence_monthly_minutes",
    "quarterly": "cadence_quarterly_minutes",
    "annual": "cadence_annual_minutes",
}
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Interval:
    minutes: int
    origin: str  # "override" | "frequency" | "default"
    shortened: bool = False  # True when the freshness window forced the interval down (FR-CADENCE-4)


def _override(source) -> int | None:
    value = (getattr(source, "extraction_rules", None) or {}).get("check_interval_minutes")
    if isinstance(value, bool) or not isinstance(value, int) or value < MIN_INTERVAL_MINUTES:
        return None  # the registry write schema rejects these; a stale bad value is ignored, not trusted
    return value


def check_interval(source) -> Interval:
    override = _override(source)
    if override is not None:
        minutes, origin = override, "override"
    elif (source.update_frequency or "") in _SETTING_BY_FREQUENCY:
        minutes, origin = getattr(settings, _SETTING_BY_FREQUENCY[source.update_frequency]), "frequency"
    else:
        minutes, origin = settings.cadence_undeclared_minutes, "default"
    window_days = source.freshness_window_days or DEFAULT_FRESHNESS_WINDOW_DAYS
    ceiling = max(MIN_INTERVAL_MINUTES, int(window_days * _MINUTES_PER_DAY / 2))
    if minutes > ceiling:
        return Interval(minutes=ceiling, origin=origin, shortened=True)
    return Interval(minutes=minutes, origin=origin)


def next_due_at(source, state) -> datetime:
    """`max(next_attempt_at, last_checked_at + interval)`; a never-checked source is due from the epoch."""
    by_interval = (
        source.last_checked_at + timedelta(minutes=check_interval(source).minutes)
        if source.last_checked_at is not None
        else _EPOCH
    )
    gate = getattr(state, "next_attempt_at", None) if state is not None else None
    return max(by_interval, gate) if gate is not None else by_interval


def is_due(source, state, now: datetime) -> bool:
    return next_due_at(source, state) <= now


def select_due(sources: Iterable, states: Mapping, now: datetime) -> list:
    """Due sources, stalest first (never-checked lead); ties break on id so the order is deterministic."""
    due = [s for s in sources if is_due(s, states.get(s.id), now)]
    return sorted(due, key=lambda s: (s.last_checked_at or _EPOCH, str(s.id)))


def backoff_minutes(level: int, base_minutes: int, cap_minutes: int) -> int:
    """Doubling back-off for an unextractable page: `base * 2**(level-1)` capped at the interval."""
    exponent = min(max(level, 1) - 1, _MAX_BACKOFF_EXPONENT)
    return min(base_minutes * 2**exponent, cap_minutes)
