"""003 T269: priority scopes are set at the entry points; a fake gate (or recorder) sees the class."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.llm_gate import Priority, current_priority, run_as_backfill
from app.core.llm_priority_middleware import LlmPriorityMiddleware
from app.models.monitoring import MonitoringTrigger
from app.workflows.source_monitor import graph


def test_http_requests_run_as_user_facing():
    seen = []
    app = FastAPI()
    app.add_middleware(LlmPriorityMiddleware)

    @app.get("/probe")
    async def async_probe():
        seen.append(("async", current_priority()))
        return {}

    @app.get("/probe-sync")
    def sync_probe():  # plain def: runs in the thread pool with a copied context
        seen.append(("sync", current_priority()))
        return {}

    client = TestClient(app)
    client.get("/probe")
    client.get("/probe-sync")
    assert seen == [("async", Priority.USER_FACING), ("sync", Priority.USER_FACING)]
    assert current_priority() is None


def test_backfill_helper_runs_the_callable_as_backfill():
    seen = []
    result = run_as_backfill(lambda a, b=2: seen.append(current_priority()) or a + b, 1, b=5)
    assert result == 6 and seen == [Priority.BACKFILL] and current_priority() is None


def test_monitor_run_alert_step_and_validation_step_carry_their_classes(db_session_factory, monkeypatch):
    seen: dict[str, Priority | None] = {}
    source = SimpleNamespace(id=uuid.uuid4())
    monkeypatch.setattr(graph.source_repo, "get_monitorable_sources", lambda *a, **k: [source])

    def fake_monitor_one(db, run, source_id, stats, **kwargs):
        seen["extract"] = current_priority()
        return True

    monkeypatch.setattr(graph, "_monitor_one_source", fake_monitor_one)
    monkeypatch.setattr(graph, "_alert_step", lambda *a, **k: seen.__setitem__("alert", current_priority()))
    monkeypatch.setattr(
        graph.candidate_validation_repo, "list_pending_needing_validation", lambda *a, **k: [uuid.uuid4()]
    )

    def fake_validate(db, candidate_id):
        seen["validate"] = current_priority()

    with db_session_factory() as db:
        graph.run_source_monitor(db, trigger=MonitoringTrigger.MANUAL, validate_candidate=fake_validate)

    assert seen == {"extract": Priority.MONITORING, "alert": Priority.MONITORING, "validate": Priority.VALIDATION}
    assert current_priority() is None
