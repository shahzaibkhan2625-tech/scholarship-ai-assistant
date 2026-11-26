"""003 T268: a refusing gate answers 503 + Retry-After on the eight student LLM routes
(contracts/api-delta.md section 1), makes no provider call, and leaks nothing. The upload route keeps
its 201 `extraction_unavailable`.

Each route is reached through the real app, auth and middleware; the one collaborator the route calls
first is replaced by a stub that makes a real `generate_text` call, so the refusal travels the same
path as in production without needing a fully populated database per route."""

import io
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core import llm, llm_gate
from app.core.llm_gate import LlmTemporarilyUnavailable, Priority, RequestType

RETRY_AT = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1, minutes=30)


class RefusingGate:
    def __init__(self) -> None:
        self.priorities: list = []

    def acquire(self, request_type, priority):
        self.priorities.append(priority)
        raise LlmTemporarilyUnavailable(RETRY_AT, "daily_allowance_spent")

    def report_provider_error(self, request_type, error):
        return None


@pytest.fixture
def gate(monkeypatch):
    refusing = RefusingGate()
    llm_gate.install_gate(refusing)

    class _NoCalls:
        def __getattr__(self, name):
            raise AssertionError("no provider call may be made when the gate refuses")

    monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=_NoCalls()))
    yield refusing
    llm_gate.install_gate(None)


def _call_llm(*_args, **_kwargs):
    return llm.generate_text("probe")


def _stub(monkeypatch, target: str, name: str = "x"):
    module_path, attr = target.rsplit(".", 1)
    import importlib

    monkeypatch.setattr(importlib.import_module(module_path), attr, _call_llm)


def _owned_application(monkeypatch):
    app_row = SimpleNamespace(id=uuid.uuid4(), scholarship_id=uuid.uuid4())
    monkeypatch.setattr("app.api.generation.application_repo.get_by_id_for_user", lambda *a, **k: app_row)
    monkeypatch.setattr("app.api.generation.scholarship_repo.get_by_id", lambda *a, **k: None)
    monkeypatch.setattr("app.api.generation.document_repo.list_application_documents_for_application", lambda *a, **k: [])
    return app_row


ROUTES = [
    ("import-cv", "post", "/profile/import-cv", "app.api.profile.extract_profile_from_text", "file"),
    ("match-url", "post", "/scholarships/match-url", "app.api.url_match.run_url_match", {"url": "https://example.org/x"}),
    ("match", "post", f"/scholarships/{uuid.uuid4()}/match", "app.api.matching.match_scholarship", None),
    ("qa", "post", f"/scholarships/{uuid.uuid4()}/qa", "app.api.qa.answer_question", {"question": "q"}),
    ("discovery", "post", "/discovery", "app.api.discovery.run_discovery", None),
    ("cv", "post", f"/applications/{uuid.uuid4()}/generate/cv", "app.api.generation.run_cv_gen", None),
    ("sop", "post", f"/applications/{uuid.uuid4()}/generate/sop", "app.api.generation.run_sop_gen", None),
]


@pytest.mark.parametrize("label, method, path, target, body", ROUTES, ids=[r[0] for r in ROUTES])
def test_refusing_gate_answers_503_with_retry_after(label, method, path, target, body, gate, authed_user, monkeypatch):
    client, headers = authed_user["client"], authed_user["headers"]
    _stub(monkeypatch, target)
    if label in {"cv", "sop"}:
        _owned_application(monkeypatch)
    if label == "match":
        monkeypatch.setattr("app.api.matching.scholarship_repo.get_by_id", lambda *a, **k: SimpleNamespace(id=uuid.uuid4()))
    if label == "discovery":
        monkeypatch.setattr("app.api.discovery._has_discovery_criteria", lambda profile: True)

    kwargs = {"headers": headers}
    if body == "file":
        kwargs["files"] = {"file": ("cv.txt", io.BytesIO(b"Jane Doe, BSc Physics"), "text/plain")}
    elif body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["code"] == "llm_temporarily_unavailable"
    assert payload["reason"] == "daily_allowance_spent"
    assert payload["detail"] == "AI features are temporarily unavailable. Try again after the retry time."
    assert set(payload) == {"detail", "code", "retry_after_seconds", "retry_at", "reason"}
    assert payload["retry_at"].startswith(RETRY_AT.strftime("%Y-%m-%dT%H:%M:%S"))
    retry_after = int(response.headers["Retry-After"])
    assert retry_after == payload["retry_after_seconds"] and 0 < retry_after <= 5400
    assert "Traceback" not in response.text
    assert gate.priorities == [Priority.USER_FACING]  # the middleware set the class; the gate was asked once


def test_the_503_is_not_applied_to_other_errors(client, authed_user, monkeypatch):
    llm_gate.install_gate(None)
    monkeypatch.setattr("app.api.qa.answer_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    from fastapi.testclient import TestClient

    from app.main import app

    local = TestClient(app, raise_server_exceptions=False)
    response = local.post(f"/scholarships/{uuid.uuid4()}/qa", json={"question": "q"}, headers=authed_user["headers"])
    assert response.status_code == 500
