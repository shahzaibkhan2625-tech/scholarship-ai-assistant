"""003 T303 (FR-OBS-2): `start_scheduler` states the running configuration in one structured record: the tick
interval, the per-frequency cadence defaults, the per-source overrides (a best-effort database read) and the
budget settings. No secret value appears in it, and a failing read never blocks startup."""

import logging
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.data.repositories import source_repo
from app.scheduling import scheduler as sched_mod

MESSAGE = "scheduler configuration"


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def startup(monkeypatch):
    import app.data.repositories.db as db_mod

    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _Session())
    sched_mod.stop_scheduler()
    yield
    sched_mod.stop_scheduler()


def _source(domain, rules=None, frequency="annual"):
    return SimpleNamespace(domain=domain, extraction_rules=rules or {}, update_frequency=frequency, freshness_window_days=None)


def _record(caplog):
    records = [r for r in caplog.records if r.getMessage() == MESSAGE]
    assert len(records) == 1
    return records[0]


def test_startup_logs_one_record_with_interval_cadence_and_budget_settings(startup, monkeypatch, caplog):
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda db, **kw: [])
    with caplog.at_level(logging.INFO):
        sched_mod.start_scheduler(360)
    record = _record(caplog)
    assert record.tick_interval_minutes == 360
    assert record.cadence_minutes == {
        "weekly": settings.cadence_weekly_minutes,
        "monthly": settings.cadence_monthly_minutes,
        "quarterly": settings.cadence_quarterly_minutes,
        "annual": settings.cadence_annual_minutes,
        "undeclared": settings.cadence_undeclared_minutes,
    }
    assert record.llm_generation_allowance == settings.llm_generation_daily_allowance
    assert record.llm_embedding_allowance == settings.llm_embedding_daily_allowance
    assert record.llm_user_facing_reserve == settings.llm_user_facing_reserve
    assert record.llm_ladder_offsets == {
        "backfill": settings.llm_ladder_offset_backfill,
        "monitoring": settings.llm_ladder_offset_monitoring,
        "validation": settings.llm_ladder_offset_validation,
    }
    assert record.llm_backfill_daily_share == settings.llm_backfill_daily_share
    assert record.llm_budget_timezone == settings.llm_budget_timezone
    assert record.source_overrides == []


def test_per_source_overrides_are_listed(startup, monkeypatch, caplog):
    sources = [_source("a.example.com", {"check_interval_minutes": 30}), _source("b.example.com"), _source("c.example.com", {"check_interval_minutes": 5})]
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda db, **kw: sources)
    with caplog.at_level(logging.INFO):
        sched_mod.start_scheduler(360)
    # only a valid override is listed (c's 5 minutes is below the minimum and is ignored by the cadence service)
    assert _record(caplog).source_overrides == [{"domain": "a.example.com", "minutes": 30}]


def test_a_failing_override_read_does_not_block_startup(startup, monkeypatch, caplog):
    def boom(db, **kw):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(source_repo, "get_monitorable_sources", boom)
    with caplog.at_level(logging.INFO):
        scheduler = sched_mod.start_scheduler(360)
    assert scheduler is not None and scheduler.running
    assert _record(caplog).source_overrides is None  # unknown, not an empty list


def test_no_secret_value_appears_in_the_record(startup, monkeypatch, caplog):
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda db, **kw: [])
    monkeypatch.setattr(settings, "gemini_api_key", "sk-very-secret-gemini-key")
    monkeypatch.setattr(settings, "jwt_secret_key", "very-secret-jwt-signing-key-0123456789")
    with caplog.at_level(logging.INFO):
        sched_mod.start_scheduler(360)
    dump = repr(_record(caplog).__dict__)
    assert "sk-very-secret-gemini-key" not in dump
    assert "very-secret-jwt-signing-key" not in dump
    assert "postgresql" not in dump  # no connection string either


def test_a_disabled_scheduler_starts_nothing_and_logs_no_configuration(startup, caplog):
    with caplog.at_level(logging.INFO):
        assert sched_mod.start_scheduler(0) is None
    assert [r for r in caplog.records if r.getMessage() == MESSAGE] == []
