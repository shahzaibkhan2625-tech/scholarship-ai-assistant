"""Settings defaults/normalisation for the 002 source-monitoring vars (T143)."""

import pytest

from app.core.config import Settings

_NEW_VARS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "ALERTS_FROM_EMAIL",
    "MONITORING_INTERVAL_MINUTES",
    "FETCH_LOG_RETENTION_DAYS",
    "HEALTH_WINDOW_N",
    "RATE_LIMIT_PER_MINUTE",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _NEW_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@localhost/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    return monkeypatch


def test_defaults(clean_env):
    s = Settings(_env_file=None)
    assert s.smtp_host is None
    assert s.smtp_port == 587
    assert s.smtp_username is None
    assert s.smtp_password is None
    assert s.alerts_from_email is None
    assert s.monitoring_interval_minutes == 60
    assert s.fetch_log_retention_days == 90
    assert s.health_window_n == 5
    assert s.rate_limit_per_minute == 120


def test_empty_smtp_host_becomes_none(clean_env):
    clean_env.setenv("SMTP_HOST", "")
    assert Settings(_env_file=None).smtp_host is None


def test_smtp_username_is_stripped(clean_env):
    clean_env.setenv("SMTP_USERNAME", "  user@example.com  ")
    assert Settings(_env_file=None).smtp_username == "user@example.com"
