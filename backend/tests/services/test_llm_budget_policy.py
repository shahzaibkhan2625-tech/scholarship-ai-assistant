"""003 T264: the pure admission policy `decide(...)` and the budget-day boundary (no database)."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.llm_gate import Priority, RequestType
from app.services.llm_budget import BudgetConfig, budget_day, decide, next_boundary

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)  # 10:00 in Los Angeles


def _config(**overrides) -> BudgetConfig:
    values = dict(
        allowance={RequestType.GENERATE: 20, RequestType.EMBED: None},
        reserve=8,
        offsets={Priority.BACKFILL: 0, Priority.MONITORING: 2, Priority.VALIDATION: 4},
        backfill_share=6,
        tz=LA,
        default_pause_seconds=900,
    )
    values.update(overrides)
    return BudgetConfig(**values)


def _decide(priority, *, used=0, backfill_used=0, paused_until=None, request_type=RequestType.GENERATE, config=None):
    return decide(
        config or _config(),
        request_type,
        priority,
        allowed_total=used,
        backfill_allowed=backfill_used,
        paused_until=paused_until,
        now=NOW,
    )


def test_user_facing_is_admitted_down_to_zero_remaining():
    assert _decide(Priority.USER_FACING, used=19).admit
    refused = _decide(Priority.USER_FACING, used=20)
    assert not refused.admit and refused.event == "denied" and refused.reason == "daily_allowance_spent"
    assert refused.retry_at == next_boundary(NOW, LA)


@pytest.mark.parametrize(
    "priority, last_used_admitted",
    [(Priority.BACKFILL, 11), (Priority.MONITORING, 9), (Priority.VALIDATION, 7)],
)
def test_ladder_floors_cut_off_validation_then_monitoring_then_backfill(priority, last_used_admitted):
    # allowance 20, reserve 8: remaining must stay above reserve + offset
    assert _decide(priority, used=last_used_admitted).admit
    refused = _decide(priority, used=last_used_admitted + 1)
    assert not refused.admit and refused.event == "deferred"
    assert refused.retry_at == next_boundary(NOW, LA)


def test_backfill_is_capped_at_its_daily_share():
    assert _decide(Priority.BACKFILL, used=5, backfill_used=5).admit
    capped = _decide(Priority.BACKFILL, used=6, backfill_used=6)
    assert not capped.admit and capped.event == "deferred"


def test_background_never_takes_the_reserve():
    for priority in (Priority.BACKFILL, Priority.MONITORING, Priority.VALIDATION):
        assert not _decide(priority, used=12).admit  # exactly the reserve (8) remains


def test_active_pause_blocks_every_class_including_user_facing():
    until = NOW + timedelta(minutes=5)
    for priority in Priority:
        decision = _decide(priority, used=0, paused_until=until)
        assert not decision.admit
        assert decision.retry_at == until
    assert _decide(Priority.USER_FACING, paused_until=until).event == "denied"
    assert _decide(Priority.USER_FACING, paused_until=until).reason == "provider_quota"
    assert _decide(Priority.MONITORING, paused_until=until).event == "deferred"


def test_expired_pause_is_ignored():
    assert _decide(Priority.MONITORING, paused_until=NOW - timedelta(seconds=1)).admit


def test_embedding_without_an_allowance_is_counted_not_limited():
    for priority in Priority:
        assert _decide(priority, used=10_000, request_type=RequestType.EMBED).admit


def test_budget_day_follows_los_angeles_midnight():
    before = datetime(2026, 11, 27, 7, 59, tzinfo=timezone.utc)  # 23:59 on the 26th in LA (PST)
    after = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)  # 00:00 on the 27th
    assert budget_day(before, LA) == date(2026, 11, 26)
    assert budget_day(after, LA) == date(2026, 11, 27)
    assert next_boundary(before, LA) == after
    assert next_boundary(after, LA) == datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)


def test_boundary_across_daylight_saving_changes():
    # Fall back on 2026-11-01: that day is 25 hours long, the next boundary is 08:00 UTC (PST).
    assert next_boundary(datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc), LA) == datetime(
        2026, 11, 2, 8, 0, tzinfo=timezone.utc
    )
    # Spring forward on 2026-03-08: 23 hours long, the next boundary is 07:00 UTC (PDT).
    assert next_boundary(datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc), LA) == datetime(
        2026, 3, 9, 7, 0, tzinfo=timezone.utc
    )
    assert budget_day(datetime(2026, 3, 9, 6, 59, tzinfo=timezone.utc), LA) == date(2026, 3, 8)
    assert budget_day(datetime(2026, 3, 9, 7, 0, tzinfo=timezone.utc), LA) == date(2026, 3, 9)


def test_config_is_built_from_settings_defaults():
    from app.core.config import settings

    config = BudgetConfig.from_settings(settings)
    assert config.allowance[RequestType.GENERATE] == 20 and config.allowance[RequestType.EMBED] is None
    assert (config.reserve, config.backfill_share, config.default_pause_seconds) == (8, 6, 900)
    assert config.offsets == {Priority.BACKFILL: 0, Priority.MONITORING: 2, Priority.VALIDATION: 4}
