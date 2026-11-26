"""003 T267: the provider wrapper in core/llm.py with a fake gate and a fake client (no network)."""

from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.core import llm, llm_gate
from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmPriorityMissing,
    LlmProviderError,
    LlmQuotaExhausted,
    Priority,
    RequestType,
    llm_priority,
)
from datetime import datetime, timezone

EVENTS: list = []


class FakeGate:
    def __init__(self, refuse: Exception | None = None) -> None:
        self.refuse = refuse
        self.reported: list = []

    def acquire(self, request_type, priority):
        EVENTS.append(("acquire", request_type, priority))
        if self.refuse is not None:
            raise self.refuse

    def report_provider_error(self, request_type, error):
        self.reported.append((request_type, error))


class FakeModels:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def generate_content(self, **kwargs):
        EVENTS.append(("client", "generate"))
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(text="ok")

    def embed_content(self, **kwargs):
        EVENTS.append(("client", "embed"))
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])


@pytest.fixture(autouse=True)
def _reset():
    EVENTS.clear()
    llm_gate.install_gate(None)
    yield
    llm_gate.install_gate(None)


def _install(monkeypatch, gate, models):
    llm_gate.install_gate(gate)
    monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=models))


def test_acquire_happens_before_the_client_call(monkeypatch):
    models = FakeModels()
    _install(monkeypatch, FakeGate(), models)
    with llm_priority(Priority.MONITORING):
        assert llm.generate_text("hi") == "ok"
    assert EVENTS == [("acquire", RequestType.GENERATE, Priority.MONITORING), ("client", "generate")]


def test_a_refusal_makes_no_client_call(monkeypatch):
    models = FakeModels()
    _install(monkeypatch, FakeGate(refuse=LlmBudgetDeferred(datetime(2026, 1, 1, tzinfo=timezone.utc))), models)
    with llm_priority(Priority.MONITORING):
        with pytest.raises(LlmBudgetDeferred):
            llm.generate_text("hi")
        with pytest.raises(LlmBudgetDeferred):
            llm.embed_texts(["a"])
    assert models.calls == 0


def test_no_priority_is_refused_before_the_gate_and_the_client(monkeypatch):
    models = FakeModels()
    _install(monkeypatch, FakeGate(), models)
    with pytest.raises(LlmPriorityMissing):
        llm.generate_text("hi")
    assert models.calls == 0 and EVENTS == []


def test_no_gate_installed_refuses_and_makes_no_client_call(monkeypatch):
    models = FakeModels()
    monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=models))
    with llm_priority(Priority.USER_FACING):
        with pytest.raises(llm_gate.LlmError):
            llm.generate_text("hi")
    assert models.calls == 0


def test_exactly_one_client_call_per_operation(monkeypatch):
    models = FakeModels(error=genai_errors.ServerError(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}))
    _install(monkeypatch, FakeGate(), models)
    with llm_priority(Priority.USER_FACING):
        with pytest.raises(LlmProviderError):
            llm.generate_text("hi")
    assert models.calls == 1  # no wrapper-level retry


def test_client_level_retries_are_never_enabled(monkeypatch):
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key-not-real")  # allow-secret
    monkeypatch.setattr(llm, "_client", None)
    client = llm.get_gemini_client()
    options = client._api_client._http_options
    assert getattr(options, "retry_options", None) is None
    monkeypatch.setattr(llm, "_client", None)


def test_api_error_is_translated_reported_and_text_is_not_copied(monkeypatch):
    gate = FakeGate()
    models = FakeModels(error=genai_errors.ClientError(
        429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "PROVIDER-SECRET-TEXT",
                        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                     "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}))
    _install(monkeypatch, gate, models)
    with llm_priority(Priority.USER_FACING):
        with pytest.raises(LlmQuotaExhausted) as caught:
            llm.generate_text("hi")
    assert "PROVIDER-SECRET-TEXT" not in str(caught.value)
    assert [(t, type(e)) for t, e in gate.reported] == [(RequestType.GENERATE, LlmQuotaExhausted)]


def test_embeddings_count_under_embed(monkeypatch):
    _install(monkeypatch, FakeGate(), FakeModels())
    with llm_priority(Priority.MONITORING):
        assert llm.embed_texts(["a"]) == [[0.1, 0.2]]
    assert EVENTS[0] == ("acquire", RequestType.EMBED, Priority.MONITORING)


def test_empty_embedding_input_makes_no_call_and_takes_no_budget(monkeypatch):
    models = FakeModels()
    _install(monkeypatch, FakeGate(), models)
    assert llm.embed_texts([]) == []
    assert EVENTS == [] and models.calls == 0


def test_not_configured_behaviour_is_preserved(monkeypatch):
    llm_gate.install_gate(FakeGate())
    monkeypatch.setattr(llm.settings, "gemini_api_key", None)
    monkeypatch.setattr(llm, "_client", None)
    with llm_priority(Priority.USER_FACING):
        with pytest.raises(llm.LlmNotConfiguredError):
            llm.generate_text("hi")
