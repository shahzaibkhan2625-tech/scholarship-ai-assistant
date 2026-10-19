"""T145: Phase 5 (002) schema shape, asserted against `Base.metadata` only —
no DB connection. Enum members are read from `column.type.enum_class`
(stored by member NAME, matching 001's enums), not `type.enums`."""

import pytest
from sqlalchemy import Boolean, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

import app.models  # noqa: F401  (eager-imports every model module)
from app.data.repositories.db import Base


def _table(name: str):
    assert name in Base.metadata.tables, f"table {name!r} missing from Base.metadata"
    return Base.metadata.tables[name]


def _col(table: str, column: str):
    t = _table(table)
    assert column in t.c, f"{table}.{column} missing"
    return t.c[column]


def _fk_targets(col) -> set[str]:
    return {fk.target_fullname for fk in col.foreign_keys}


def _server_default_text(col) -> str | None:
    return None if col.server_default is None else str(col.server_default.arg).lower()


def _members(col) -> set[str]:
    return {m.value for m in col.type.enum_class}


# --- additive columns on existing tables -----------------------------------


def test_source_registry_listing_page_url():
    col = _col("source_registry", "listing_page_url")
    assert isinstance(col.type, Text) and col.nullable


def test_source_registry_freshness_window_days():
    col = _col("source_registry", "freshness_window_days")
    assert isinstance(col.type, Integer) and col.nullable


def test_source_fetch_log_fetched_url():
    col = _col("source_fetch_log", "fetched_url")
    assert isinstance(col.type, Text) and col.nullable


def test_source_fetch_log_used_homepage_fallback():
    col = _col("source_fetch_log", "used_homepage_fallback")
    assert isinstance(col.type, Boolean) and not col.nullable
    assert _server_default_text(col) in {"false", "'false'"}


def test_source_fetch_log_monitoring_run_id():
    col = _col("source_fetch_log", "monitoring_run_id")
    assert isinstance(col.type, UUID) and col.nullable
    assert _fk_targets(col) == {"monitoring_runs.id"}


def test_source_fetch_log_monitoring_run_id_is_indexed():
    t = _table("source_fetch_log")
    assert any([c.name for c in ix.columns] == ["monitoring_run_id"] for ix in t.indexes)


# --- monitoring_runs --------------------------------------------------------


def test_monitoring_runs_columns():
    t = _table("monitoring_runs")
    assert "user_id" not in t.c  # system-owned
    assert [c.name for c in t.primary_key.columns] == ["id"]
    assert isinstance(t.c.id.type, UUID)
    started = t.c.started_at
    assert isinstance(started.type, DateTime) and started.type.timezone and not started.nullable
    assert "now" in _server_default_text(started)
    ended = t.c.ended_at
    assert isinstance(ended.type, DateTime) and ended.type.timezone and ended.nullable
    for name in ("sources_processed", "sources_failed", "changes_detected"):
        col = t.c[name]
        assert isinstance(col.type, Integer) and not col.nullable


def test_monitoring_runs_enums():
    assert _members(_col("monitoring_runs", "trigger")) == {"scheduled", "manual"}
    assert _members(_col("monitoring_runs", "status")) == {"running", "completed", "failed"}
    for name in ("trigger", "status"):
        col = _col("monitoring_runs", name)
        assert not col.nullable
        assert col.type.native_enum is False


def test_monitoring_enums_stored_by_member_name():
    from app.models.monitoring import MonitoringStatus, MonitoringTrigger

    assert set(_col("monitoring_runs", "trigger").type.enums) == {m.name for m in MonitoringTrigger}
    assert set(_col("monitoring_runs", "status").type.enums) == {m.name for m in MonitoringStatus}


# --- alerts -----------------------------------------------------------------


def test_alerts_columns_and_fks():
    t = _table("alerts")
    assert [c.name for c in t.primary_key.columns] == ["id"]
    for name, target, nullable in [
        ("user_id", "users.id", False),
        ("scholarship_id", "scholarships.id", False),
        ("match_id", "matches.id", True),
        ("monitoring_run_id", "monitoring_runs.id", True),
    ]:
        col = t.c[name]
        assert isinstance(col.type, UUID)
        assert col.nullable is nullable, name
        assert _fk_targets(col) == {target}, name
    for name in ("change_summary", "channels_requested"):
        assert isinstance(t.c[name].type, JSONB) and not t.c[name].nullable
    for name in ("delivered_in_app_at", "delivered_email_at"):
        assert isinstance(t.c[name].type, DateTime) and t.c[name].type.timezone and t.c[name].nullable
    assert isinstance(t.c.email_error.type, Text) and t.c.email_error.nullable
    created = t.c.created_at
    assert isinstance(created.type, DateTime) and created.type.timezone and not created.nullable
    assert "now" in _server_default_text(created)


def test_alerts_indexes():
    t = _table("alerts")
    indexed = {c.name for ix in t.indexes for c in ix.columns}
    assert {"user_id", "scholarship_id"} <= indexed


def test_alerts_read_at_is_nullable_timestamptz_without_default():
    col = _col("alerts", "read_at")
    assert isinstance(col.type, DateTime) and col.type.timezone
    assert col.nullable
    assert col.server_default is None and col.default is None


# --- alert_preferences ------------------------------------------------------


def test_alert_preferences_user_id_is_the_primary_key():
    t = _table("alert_preferences")
    assert [c.name for c in t.primary_key.columns] == ["user_id"]
    assert _fk_targets(t.c.user_id) == {"users.id"}
    assert "id" not in t.c


def test_alert_preferences_columns():
    t = _table("alert_preferences")
    for name in ("in_app_enabled", "email_enabled"):
        col = t.c[name]
        assert isinstance(col.type, Boolean) and not col.nullable
        assert _server_default_text(col) in {"true", "'true'"}, name
    updated = t.c.updated_at
    assert isinstance(updated.type, DateTime) and updated.type.timezone and not updated.nullable
    assert "now" in _server_default_text(updated)
    assert updated.onupdate is not None  # approved extra: ORM onupdate=now()


# --- candidate_source_validations -------------------------------------------


def test_candidate_source_validations_columns():
    t = _table("candidate_source_validations")
    assert "user_id" not in t.c
    assert [c.name for c in t.primary_key.columns] == ["id"]
    fk = t.c.candidate_source_id
    assert not fk.nullable and _fk_targets(fk) == {"candidate_sources.id"}
    assert {c.name for ix in t.indexes for c in ix.columns} >= {"candidate_source_id"}
    checked = t.c.checked_at
    assert isinstance(checked.type, DateTime) and checked.type.timezone and not checked.nullable
    assert "now" in _server_default_text(checked)
    for name in ("reachable", "extractable"):
        assert isinstance(t.c[name].type, Boolean) and not t.c[name].nullable
    for name in ("reachable_detail", "extractable_detail"):
        assert isinstance(t.c[name].type, Text) and t.c[name].nullable
    signals = t.c.official_signals
    assert isinstance(signals.type, JSONB) and not signals.nullable
    assert _server_default_text(signals) is not None and "{}" in _server_default_text(signals)


# --- additive-only guard on 001 objects --------------------------------------


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("source_registry", "domain"),
        ("source_registry", "status"),
        ("source_fetch_log", "retry_count"),
        ("source_fetch_log", "items_found"),
        ("scholarships", "lifecycle_status"),
    ],
)
def test_existing_001_columns_still_present(table, column):
    assert column in _table(table).c


def test_lifecycle_status_enum_unchanged():
    col = _col("scholarships", "lifecycle_status")
    assert _members(col) == {
        "newly_discovered",
        "verified",
        "unverified",
        "updated",
        "expired",
        "closed",
        "reopened",
        "stale",
        "source_unavailable",
    }
