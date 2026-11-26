"""003 T251: quota-governance settings: documented defaults, and rejected invalid values."""

import pytest

from app.core.config import Settings


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost/db")  # allow-secret
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-0123456789abcdef-0123456789")  # allow-secret
    return monkeypatch


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_defaults_load_with_nothing_set(env):
    s = _settings()
    assert s.llm_generation_daily_allowance == 20
    assert s.llm_embedding_daily_allowance is None  # counted, not limited
    assert s.llm_user_facing_reserve == 8
    assert (s.llm_ladder_offset_backfill, s.llm_ladder_offset_monitoring, s.llm_ladder_offset_validation) == (0, 2, 4)
    assert s.llm_backfill_daily_share == 6
    assert s.llm_budget_timezone == "America/Los_Angeles"
    assert s.llm_default_pause_seconds == 900
    assert s.llm_unavailable_retry_seconds == 60
    assert s.llm_ledger_retention_days == 90


def test_cadence_and_skip_defaults(env):
    s = _settings()
    assert s.cadence_weekly_minutes == 1440
    assert s.cadence_monthly_minutes == 10080
    assert s.cadence_quarterly_minutes == 10080
    assert s.cadence_annual_minutes == 10080
    assert s.cadence_undeclared_minutes == 1440
    assert s.forced_full_recheck_days == 7
    assert s.unextractable_backoff_base_minutes == 60
    assert s.noise_window_checks == 10
    assert s.noise_threshold == 0.8
    assert s.rules_anomaly_low_ratio == 0.5
    assert s.rules_anomaly_high_ratio == 2.0


def test_overrides_are_read_from_the_environment(env):
    env.setenv("LLM_GENERATION_DAILY_ALLOWANCE", "1500")
    env.setenv("LLM_USER_FACING_RESERVE", "100")
    env.setenv("LLM_BUDGET_TIMEZONE", "UTC")
    s = _settings()
    assert (s.llm_generation_daily_allowance, s.llm_user_facing_reserve, s.llm_budget_timezone) == (1500, 100, "UTC")


def test_reserve_must_be_below_the_allowance(env):
    env.setenv("LLM_USER_FACING_RESERVE", "20")
    with pytest.raises(ValueError):
        _settings()


@pytest.mark.parametrize(
    "name",
    ["LLM_LADDER_OFFSET_BACKFILL", "LLM_LADDER_OFFSET_MONITORING", "LLM_LADDER_OFFSET_VALIDATION"],
)
def test_negative_ladder_offsets_are_rejected(env, name):
    env.setenv(name, "-1")
    with pytest.raises(ValueError):
        _settings()


def test_unknown_timezone_is_rejected(env):
    env.setenv("LLM_BUDGET_TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(ValueError):
        _settings()
