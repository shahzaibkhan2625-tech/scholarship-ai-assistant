"""003 T253: quota-governance schema shape, asserted against `Base.metadata` only (no DB)."""

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text

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


def _pk(table: str) -> set[str]:
    return {c.name for c in _table(table).primary_key.columns}


def _server_default_text(col) -> str | None:
    return None if col.server_default is None else str(col.server_default.arg).lower()


def test_llm_budget_ledger():
    assert _pk("llm_budget_ledger") == {"budget_day", "request_type", "priority_class", "event"}
    assert isinstance(_col("llm_budget_ledger", "budget_day").type, Date)
    count = _col("llm_budget_ledger", "count")
    assert isinstance(count.type, Integer) and not count.nullable
    assert _server_default_text(count) == "0"
    assert isinstance(_col("llm_budget_ledger", "updated_at").type, DateTime)


def test_llm_budget_state():
    assert _pk("llm_budget_state") == {"request_type"}
    for name, kind in [
        ("paused_until", DateTime),
        ("pause_reason", String),
        ("pause_set_at", DateTime),
        ("observed_limit", Integer),
    ]:
        col = _col("llm_budget_state", name)
        assert isinstance(col.type, kind) and col.nullable


def test_source_check_state():
    assert _pk("source_check_state") == {"source_id"}
    assert _fk_targets(_col("source_check_state", "source_id")) == {"source_registry.id"}
    nullable = [
        "fingerprint", "fingerprint_at", "etag", "last_modified", "last_extraction_outcome",
        "last_full_extraction_at", "next_attempt_at", "rules_review_reason",
    ]
    for name in nullable:
        assert _col("source_check_state", name).nullable, name
    assert _col("source_check_state", "fingerprint").type.length == 64
    assert isinstance(_col("source_check_state", "etag").type, Text)
    for name in ["validators_trusted", "rules_review_needed"]:
        col = _col("source_check_state", name)
        assert isinstance(col.type, Boolean) and not col.nullable
    for name in ["backoff_level", "checks_window", "changed_window"]:
        col = _col("source_check_state", name)
        assert isinstance(col.type, Integer) and not col.nullable


def test_classify_pending():
    assert _pk("classify_pending") == {"scholarship_id"}
    assert _fk_targets(_col("classify_pending", "scholarship_id")) == {"scholarships.id"}
    assert not _col("classify_pending", "queued_at").nullable
    attempts = _col("classify_pending", "attempts")
    assert isinstance(attempts.type, Integer) and not attempts.nullable
    assert _col("classify_pending", "last_reason").type.length == 32
    assert _col("classify_pending", "last_attempt_at").nullable


def test_fetch_log_outcome_column():
    col = _col("source_fetch_log", "outcome")
    assert isinstance(col.type, String) and col.type.length == 24 and col.nullable
    assert col.server_default is None


def test_monitoring_run_counters():
    for name in ["sources_skipped", "sources_deferred"]:
        col = _col("monitoring_runs", name)
        assert isinstance(col.type, Integer) and not col.nullable
        assert _server_default_text(col) == "0"
