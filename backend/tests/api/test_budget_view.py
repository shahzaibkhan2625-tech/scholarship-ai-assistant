"""003 T459 (FR-OBS-5, SC-013): `GET /monitoring/budget`, the operator-only budget and cadence view.

401 without a token, 403 for a signed-in non-operator, 200 for an operator with the body of
`contracts/api-delta.md` section 5; the numbers equal the metrics collector's for the same ledger; no write
method exists; no secret or page content in the body. Real (rolled-back) DB through the test client."""

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from prometheus_client import CollectorRegistry

from app.core import metrics
from app.core.config import settings
from app.data.repositories import check_state_repo, classify_pending_repo, llm_budget_repo, source_repo
from app.models.scholarship import Scholarship
from app.schemas.source import SourceRegistryCreate

URL = "/monitoring/budget"


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


@pytest.fixture
def operator(client, make_user):
    return {"client": client, "headers": make_user(is_operator=True)["headers"]}


def _today():
    return datetime.now(timezone.utc).astimezone(ZoneInfo(settings.llm_budget_timezone)).date()


def _seed(session):
    day = _today()
    for cls, event, n in (
        ("user_facing", "allowed", 3), ("monitoring", "allowed", 2), ("monitoring", "deferred", 4),
        ("user_facing", "denied", 1), ("monitoring", "avoided_unchanged", 7), ("monitoring", "avoided_rules", 2),
    ):
        llm_budget_repo.increment(session, day, "generate", cls, event, by=n)
    session.commit()


def test_auth_levels(client, authed_user, operator):
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    forbidden = client.get(URL, headers=authed_user["headers"])
    assert forbidden.status_code == 403
    assert forbidden.json() == {"detail": "Operator access required"}
    assert operator["client"].get(URL, headers=operator["headers"]).status_code == 200


def test_the_body_has_the_documented_shape(operator, session):
    _seed(session)
    body = operator["client"].get(URL, headers=operator["headers"]).json()
    assert set(body) == {"budget_day", "reset_at", "classify_pending", "request_types", "sources"}
    assert body["budget_day"] == _today().isoformat()
    generate = body["request_types"]["generate"]
    assert {
        "allowance", "reserve", "used", "remaining", "deferred", "denied", "provider_exhausted", "discrepancy",
        "avoided_unchanged", "avoided_rules", "paused_until", "observed_limit",
    } <= set(generate)
    assert generate["allowance"] == settings.llm_generation_daily_allowance
    assert generate["reserve"] == settings.llm_user_facing_reserve
    assert generate["used"] == {"user_facing": 3, "backfill": 0, "monitoring": 2, "validation": 0}
    assert generate["remaining"] == settings.llm_generation_daily_allowance - 5
    assert (generate["deferred"], generate["denied"]) == (4, 1)
    assert (generate["avoided_unchanged"], generate["avoided_rules"]) == (7, 2)
    assert generate["paused_until"] is None
    embed = body["request_types"]["embed"]
    assert embed["allowance"] == settings.llm_embedding_daily_allowance and "used" in embed


def test_the_numbers_equal_the_metrics_collectors(operator, session, db_session_factory):
    _seed(session)
    body = operator["client"].get(URL, headers=operator["headers"]).json()["request_types"]["generate"]
    registry = CollectorRegistry()
    registry.register(metrics.BudgetCollector(session_factory=db_session_factory))
    value = lambda name, **labels: registry.get_sample_value(name, {"request_type": "generate", **labels})  # noqa: E731
    assert body["remaining"] == value("llm_budget_remaining")
    for cls, used in body["used"].items():
        assert used == value("llm_budget_used", priority_class=cls)
    assert body["deferred"] == value("llm_budget_events", priority_class="monitoring", event="deferred")
    assert body["denied"] == value("llm_budget_events", priority_class="user_facing", event="denied")


def test_the_classification_queue_size_is_in_the_body(operator, session):
    before = operator["client"].get(URL, headers=operator["headers"]).json()["classify_pending"]
    row = Scholarship(name=f"Queued {uuid.uuid4().hex[:6]}", official_scholarship_url="https://x.example.com")
    session.add(row)
    session.flush()
    classify_pending_repo.enqueue(session, row.id, "budget_deferred")
    session.commit()
    assert operator["client"].get(URL, headers=operator["headers"]).json()["classify_pending"] == before + 1


def test_sources_list_the_interval_in_force_and_the_flags(operator, session):
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Budget View Source", source_type="gov", official_status="official",
            domain=f"bv-{uuid.uuid4().hex[:8]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active", update_frequency="annual",
            extraction_rules={"check_interval_minutes": 45, "note": "PAGE-CONTENT-MARKER"},
        ),
    )
    check_state_repo.update(session, source.id, checks_window=10, changed_window=10, rules_review_needed=True)
    session.commit()
    body = operator["client"].get(URL, headers=operator["headers"]).json()
    [row] = [r for r in body["sources"] if r["domain"] == source.domain]
    assert row["interval_minutes"] == 45 and row["interval_source"] == "override"
    assert row["noisy"] is True and row["rules_review_needed"] is True
    assert row["last_checked_at"] is None and row["next_due_at"] is None
    assert set(row) == {
        "domain", "interval_minutes", "interval_source", "last_checked_at", "next_due_at", "noisy",
        "rules_review_needed",
    }


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_write_method_exists(operator, method):
    response = getattr(operator["client"], method)(URL, headers=operator["headers"])
    assert response.status_code == 405


def test_no_secret_or_page_content_in_the_body(operator, session, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "sk-very-secret-gemini-key")
    source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Leak Check", source_type="gov", official_status="official",
            domain=f"leak-{uuid.uuid4().hex[:8]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active", extraction_rules={"note": "PAGE-CONTENT-MARKER"},
        ),
    )
    session.commit()
    text = operator["client"].get(URL, headers=operator["headers"]).text
    assert "sk-very-secret-gemini-key" not in text
    assert "PAGE-CONTENT-MARKER" not in text
    assert "postgresql" not in text and "Traceback" not in text
