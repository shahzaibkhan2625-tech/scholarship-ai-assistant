"""The LLM budget day (003, research R-05): the calendar day in the configured timezone, which
defaults to the provider reset at midnight Pacific. Pure time arithmetic, no I/O."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def budget_day(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def next_boundary(now: datetime, tz: ZoneInfo) -> datetime:
    """The next local midnight, as an aware UTC instant (correct across daylight-saving changes)."""
    tomorrow = budget_day(now, tz) + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=tz).astimezone(timezone.utc)
