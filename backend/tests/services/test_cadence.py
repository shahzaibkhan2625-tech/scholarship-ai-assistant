"""T294: pure cadence service (003 US3, FR-CADENCE-1..5, SC-004). No database, no clock."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import cadence

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
DAY = 1440


def _source(frequency=None, *, rules=None, window=None, checked=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        update_frequency=frequency,
        extraction_rules=rules or {},
        freshness_window_days=window,
        last_checked_at=checked,
    )


def _state(next_attempt_at=None):
    return SimpleNamespace(next_attempt_at=next_attempt_at)


def test_interval_by_update_frequency_defaults():
    assert cadence.check_interval(_source("weekly")).minutes == 1 * DAY
    assert cadence.check_interval(_source("monthly")).minutes == 7 * DAY
    assert cadence.check_interval(_source("quarterly")).minutes == 7 * DAY
    assert cadence.check_interval(_source("annual")).minutes == 7 * DAY
    assert cadence.check_interval(_source(None)).minutes == 1 * DAY  # undeclared: conservative daily
    assert cadence.check_interval(_source("fortnightly")).minutes == 1 * DAY  # unknown word: same default


def test_override_takes_precedence_over_frequency():
    interval = cadence.check_interval(_source("annual", rules={"check_interval_minutes": 120}))
    assert interval.minutes == 120
    assert interval.origin == "override"


def test_invalid_override_is_ignored():
    for bad in (5, 0, -3, "60", 90.5, True, None):
        assert cadence.check_interval(_source("weekly", rules={"check_interval_minutes": bad})).minutes == DAY


def test_interval_never_exceeds_the_freshness_window():
    # FR-CADENCE-4: shortened (to half the window, like the forced re-check) and flagged
    interval = cadence.check_interval(_source("annual", window=6))
    assert interval.minutes == 3 * DAY
    assert interval.shortened is True
    # an override longer than the window is shortened too
    assert cadence.check_interval(_source("weekly", rules={"check_interval_minutes": 20 * DAY}, window=10)).minutes == 5 * DAY
    # an interval already inside the window is untouched and not flagged
    ok = cadence.check_interval(_source("annual", window=60))
    assert ok.minutes == 7 * DAY and ok.shortened is False


def test_due_is_max_of_next_attempt_and_last_check_plus_interval():
    source = _source("weekly", checked=NOW - timedelta(hours=30))
    assert cadence.is_due(source, _state(), NOW) is True  # 30h > 1 day
    assert cadence.is_due(source, _state(NOW + timedelta(minutes=5)), NOW) is False  # back-off gate wins
    assert cadence.is_due(source, _state(NOW - timedelta(minutes=5)), NOW) is True
    fresh = _source("weekly", checked=NOW - timedelta(hours=2))
    assert cadence.is_due(fresh, _state(), NOW) is False
    assert cadence.is_due(fresh, _state(NOW - timedelta(days=3)), NOW) is False  # interval still wins
    assert cadence.next_due_at(fresh, _state(NOW + timedelta(days=3))) == NOW + timedelta(days=3)


def test_never_checked_source_is_due_unless_gated():
    source = _source("annual")
    assert cadence.is_due(source, None, NOW) is True  # no state row yet
    assert cadence.is_due(source, _state(NOW + timedelta(minutes=1)), NOW) is False


def test_select_due_is_stalest_first_with_never_checked_leading():
    old = _source("weekly", checked=NOW - timedelta(days=5))
    older = _source("weekly", checked=NOW - timedelta(days=9))
    never = _source("weekly")
    quiet = _source("weekly", checked=NOW - timedelta(hours=1))
    gated = _source("weekly", checked=NOW - timedelta(days=30))
    states = {gated.id: _state(NOW + timedelta(hours=1))}
    due = cadence.select_due([old, quiet, never, gated, older], states, NOW)
    assert due == [never, older, old]


def test_select_due_order_is_deterministic_on_ties():
    a, b = _source("weekly"), _source("weekly")
    first = cadence.select_due([a, b], {}, NOW)
    assert cadence.select_due([b, a], {}, NOW) == first


def test_backoff_doubles_and_is_capped_at_the_interval():
    base, cap = 60, 1 * DAY
    assert [cadence.backoff_minutes(level, base, cap) for level in (1, 2, 3, 4, 5, 6)] == [60, 120, 240, 480, 960, 1440]
    assert cadence.backoff_minutes(40, base, cap) == cap  # no overflow at a huge level
    assert cadence.backoff_minutes(0, base, cap) == 60  # level 0 is treated as the first step


def _simulate(source, days, tick_minutes=60):
    checks, t = 0, NOW
    end = NOW + timedelta(days=days)
    while t < end:
        if cadence.is_due(source, None, t):
            checks += 1
            source.last_checked_at = t
        t += timedelta(minutes=tick_minutes)
    return checks


def test_fourteen_day_simulation_annual_and_weekly():
    annual, weekly = _source("annual"), _source("weekly")
    assert _simulate(annual, 14) <= 3
    assert _simulate(weekly, 14) <= 15
    assert _simulate(_source("annual"), 14) >= 2  # it is not starved either


def test_no_record_goes_stale_because_of_the_schedule():
    # the longest gap between checks is never above the freshness window (default window used here)
    from app.services.lifecycle import DEFAULT_FRESHNESS_WINDOW_DAYS

    for frequency in ("weekly", "monthly", "quarterly", "annual", None):
        source = _source(frequency)
        assert cadence.check_interval(source).minutes <= DEFAULT_FRESHNESS_WINDOW_DAYS * DAY
