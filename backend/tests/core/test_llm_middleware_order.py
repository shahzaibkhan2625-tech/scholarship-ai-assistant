"""003 T273: the `user_facing` middleware sits inside 010's body-limit and rate-limit middleware, so a
request refused by either never reaches the gate; and the gate is installed at application start."""

from fastapi.testclient import TestClient

from app.core import llm_gate
from app.core.body_limit import BodyLimitMiddleware
from app.core.llm_priority_middleware import LlmPriorityMiddleware
from app.core.rate_limit import RateLimitMiddleware
from app.main import app, rate_limiter


class CountingGate:
    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self, request_type, priority):
        self.acquired += 1

    def report_provider_error(self, request_type, error):
        return None


def _order() -> list:
    return [m.cls for m in app.user_middleware]  # outermost first


def test_priority_middleware_is_the_innermost():
    order = _order()
    assert order.index(LlmPriorityMiddleware) > order.index(BodyLimitMiddleware)
    assert order.index(LlmPriorityMiddleware) > order.index(RateLimitMiddleware)


def test_a_request_refused_by_the_body_limit_never_reaches_the_gate():
    gate = CountingGate()
    llm_gate.install_gate(gate)
    try:
        response = TestClient(app).post(
            "/profile/import-cv", headers={"Content-Length": str(10**9)}, content=b"x"
        )
        assert response.status_code == 413
        assert gate.acquired == 0
    finally:
        llm_gate.install_gate(None)


def test_a_request_refused_by_the_rate_limit_never_reaches_the_gate(monkeypatch):
    gate = CountingGate()
    llm_gate.install_gate(gate)
    monkeypatch.setattr(rate_limiter, "check", lambda key: 30)  # refuse: 30 seconds until a token
    try:
        response = TestClient(app).post(
            "/scholarships/00000000-0000-0000-0000-000000000000/qa", json={"question": "q"}
        )
        assert response.status_code == 429
        assert gate.acquired == 0
    finally:
        llm_gate.install_gate(None)


def test_lifespan_installs_a_database_gate_and_removes_it_on_shutdown(monkeypatch):
    from app.services.llm_budget import DbLlmGate

    monkeypatch.setattr("app.scheduling.scheduler.start_scheduler", lambda: None)
    monkeypatch.setattr("app.scheduling.scheduler.stop_scheduler", lambda: None)
    llm_gate.install_gate(None)
    with TestClient(app):
        assert isinstance(llm_gate.get_gate(), DbLlmGate)
    assert not isinstance(llm_gate.get_gate(), DbLlmGate)
