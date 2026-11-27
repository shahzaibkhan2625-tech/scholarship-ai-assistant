"""003 T302 (FR-OBS-1/4, FR-SKIP-7): the scrape-time budget collector.

The numbers are read from the ledger, pause and check-state tables when `/metrics` is scraped, so a freshly
built collector (a restart, or a second worker) shows the same values. Series and labels follow
`contracts/api-delta.md` section 4. Real (rolled-back) DB; fixed clock."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.core import metrics
from app.core.config import settings
from app.data.repositories import check_state_repo, classify_pending_repo, llm_budget_repo, source_repo
from app.models.scholarship import Scholarship
from app.schemas.source import SourceRegistryCreate

NOW = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)  # 10:00 in Los Angeles, budget day 2026-11-27
DAY = date(2026, 11, 27)
RESET = datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)  # next LA midnight (PST)


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


def _collector(db_session_factory):
    return metrics.BudgetCollector(session_factory=db_session_factory, clock=lambda: NOW)


def _registry(db_session_factory):
    registry = CollectorRegistry()
    registry.register(_collector(db_session_factory))
    return registry


def _seed(db):
    inc = llm_budget_repo.increment
    inc(db, DAY, "generate", "user_facing", "allowed", by=3)
    inc(db, DAY, "generate", "monitoring", "allowed", by=2)
    inc(db, DAY, "generate", "monitoring", "deferred", by=4)
    inc(db, DAY, "generate", "user_facing", "denied", by=1)
    inc(db, DAY, "generate", "monitoring", "avoided_unchanged", by=7)
    inc(db, DAY, "generate", "monitoring", "avoided_rules", by=2)
    inc(db, DAY, "generate", "monitoring", "provider_exhausted", by=1)
    inc(db, DAY, "generate", "monitoring", "discrepancy", by=1)
    inc(db, DAY, "embed", "user_facing", "allowed", by=5)
    inc(db, date(2026, 11, 26), "generate", "monitoring", "allowed", by=9)  # yesterday: not today's number
    db.commit()


def _value(registry, name, labels=None):
    return registry.get_sample_value(name, labels or {})


def test_series_match_the_ledger_for_today(db, db_session_factory):
    _seed(db)
    r = _registry(db_session_factory)
    assert _value(r, "llm_budget_allowance", {"request_type": "generate"}) == settings.llm_generation_daily_allowance
    assert _value(r, "llm_budget_reserve", {"request_type": "generate"}) == settings.llm_user_facing_reserve
    assert _value(r, "llm_budget_used", {"request_type": "generate", "priority_class": "user_facing"}) == 3
    assert _value(r, "llm_budget_used", {"request_type": "generate", "priority_class": "monitoring"}) == 2
    assert _value(r, "llm_budget_used", {"request_type": "generate", "priority_class": "validation"}) == 0
    assert _value(r, "llm_budget_remaining", {"request_type": "generate"}) == settings.llm_generation_daily_allowance - 5
    events = lambda cls, ev: _value(  # noqa: E731
        r, "llm_budget_events", {"request_type": "generate", "priority_class": cls, "event": ev}
    )
    assert events("monitoring", "deferred") == 4
    assert events("user_facing", "denied") == 1
    assert events("monitoring", "avoided_unchanged") == 7
    assert events("monitoring", "avoided_rules") == 2
    assert events("monitoring", "provider_exhausted") == 1
    assert events("monitoring", "discrepancy") == 1


def test_embeddings_are_counted_but_not_limited_when_no_allowance_is_set(db, db_session_factory, monkeypatch):
    monkeypatch.setattr(settings, "llm_embedding_daily_allowance", None)
    _seed(db)
    r = _registry(db_session_factory)
    assert _value(r, "llm_budget_used", {"request_type": "embed", "priority_class": "user_facing"}) == 5
    assert _value(r, "llm_budget_allowance", {"request_type": "embed"}) is None  # unset: no limit series
    assert _value(r, "llm_budget_remaining", {"request_type": "embed"}) is None


def test_seconds_to_reset_follows_the_budget_day_boundary(db, db_session_factory):
    r = _registry(db_session_factory)
    assert _value(r, "llm_budget_seconds_to_reset") == (RESET - NOW).total_seconds()


def test_the_provider_pause_is_exposed_as_a_unix_time_and_zero_when_none(db, db_session_factory):
    r = _registry(db_session_factory)
    assert _value(r, "llm_provider_paused_until_timestamp", {"request_type": "generate"}) == 0
    until = NOW + timedelta(hours=2)
    llm_budget_repo.set_pause(db, "generate", until, "daily_quota", 20)
    db.commit()
    r = _registry(db_session_factory)
    assert _value(r, "llm_provider_paused_until_timestamp", {"request_type": "generate"}) == until.timestamp()


def test_a_freshly_built_collector_shows_the_same_values(db, db_session_factory):
    _seed(db)
    first = generate_latest(_registry(db_session_factory))
    second = generate_latest(_registry(db_session_factory))  # a restart or a second worker
    assert first == second
    assert b"llm_budget_used" in first


def test_source_check_noisy_reflects_the_state_table(db, db_session_factory):
    def make(label, checks, changed):
        source = source_repo.upsert_source_by_domain(
            db,
            SourceRegistryCreate(
                name=f"Noisy {label}", source_type="gov", official_status="official",
                domain=f"noisy-{label}-{uuid.uuid4().hex[:8]}.example.com", access_method="web",
                discovery_role=True, reliability_level="high", status="active",
            ),
        )
        check_state_repo.update(db, source.id, checks_window=checks, changed_window=changed)
        db.commit()
        return source

    noisy, calm = make("a", 10, 9), make("b", 10, 1)
    r = _registry(db_session_factory)
    assert _value(r, "source_check_noisy", {"domain": noisy.domain}) == 1
    assert _value(r, "source_check_noisy", {"domain": calm.domain}) == 0


def test_the_classify_queue_size_is_a_gauge(db, db_session_factory):
    r = _registry(db_session_factory)
    before = _value(r, "classify_pending_size")
    row = Scholarship(name=f"Queued {uuid.uuid4().hex[:6]}", official_scholarship_url="https://x.example.com")
    db.add(row)
    db.flush()
    classify_pending_repo.enqueue(db, row.id, "budget_deferred")
    db.commit()
    assert _value(_registry(db_session_factory), "classify_pending_size") == before + 1


def test_a_failing_budget_store_never_breaks_the_scrape(db_session_factory):
    def boom():
        raise RuntimeError("database unreachable")

    registry = CollectorRegistry()
    registry.register(metrics.BudgetCollector(session_factory=boom, clock=lambda: NOW))
    assert generate_latest(registry) == b""  # no budget series, and no exception


def test_labels_are_never_source_ids_or_user_ids(db, db_session_factory):
    _seed(db)
    allowed = {"request_type", "priority_class", "event", "domain"}
    for family in _registry(db_session_factory).collect():
        for sample in family.samples:
            assert set(sample.labels) <= allowed, sample


def test_metrics_route_requires_a_token_and_serves_the_budget_series(authed_user, db_session_factory, monkeypatch):
    client = authed_user["client"]
    assert client.get("/metrics").status_code == 401
    monkeypatch.setattr(metrics.BUDGET_COLLECTOR, "session_factory", db_session_factory)
    body = client.get("/metrics", headers=authed_user["headers"]).text
    assert "llm_budget_allowance" in body
    assert "llm_budget_seconds_to_reset" in body
