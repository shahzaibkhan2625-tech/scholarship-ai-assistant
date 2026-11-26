"""003 T454 (FR-ERR-6, SC-011, audit B-57): on each LLM-backed student route a provider quota error,
an outage, an unparseable reply and a missing key all answer the documented 503 with `Retry-After`,
never a 500. Anything that is not an `LlmError` is not reported as an AI outage.

Like test_llm_unavailable.py, each route is reached through the real app, auth and middleware, and the
collaborator the route calls first is replaced by a stub that makes a real `generate_text` call, so
the failure travels the path it takes in production. Quota, outage and missing key are produced at the
provider client (the translation in `app.core.llm` is real); an unparseable reply is raised as
`LlmResponseUnparseable`, and the real parse sites are tested separately below. The document upload
keeps its 201 `extraction_unavailable` for every kind (tests/api/test_documents_api.py walks the whole
`LlmError` family through the real route)."""

import ast
import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from app.core import llm, llm_gate
from app.core.llm import LlmNotConfiguredError
from app.core.llm_gate import LlmError, LlmResponseUnparseable

_QUOTA = {"error": {"code": 429, "message": "quota exceeded: SECRET-PROVIDER-TEXT", "status": "RESOURCE_EXHAUSTED"}}
_OUTAGE = {"error": {"code": 503, "message": "overloaded: SECRET-PROVIDER-TEXT", "status": "UNAVAILABLE"}}


class AdmittingGate:
    def __init__(self) -> None:
        self.errors: list = []

    def acquire(self, request_type, priority):
        return None

    def report_provider_error(self, request_type, error):
        self.errors.append(error)


class _Models:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def generate_content(self, *args, **kwargs):
        raise self._error

    def embed_content(self, *args, **kwargs):
        raise self._error


def _install_failure(monkeypatch, kind: str) -> None:
    llm_gate.install_gate(AdmittingGate())
    if kind == "quota":
        client = SimpleNamespace(models=_Models(genai_errors.ClientError(429, _QUOTA)))
        monkeypatch.setattr(llm, "get_gemini_client", lambda: client)
    elif kind == "outage":
        client = SimpleNamespace(models=_Models(genai_errors.ServerError(503, _OUTAGE)))
        monkeypatch.setattr(llm, "get_gemini_client", lambda: client)
    elif kind == "not_configured":
        monkeypatch.setattr(llm.settings, "gemini_api_key", "")
        monkeypatch.setattr(llm, "_client", None)
    # "unparseable" is raised by the stub itself (see _call_llm)


@pytest.fixture(autouse=True)
def _uninstall_gate():
    yield
    llm_gate.install_gate(None)


KINDS = {
    "quota": "provider_quota",
    "outage": "provider_unavailable",
    "unparseable": "provider_response_invalid",
    "not_configured": "not_configured",
}


def _call_llm_for(kind: str):
    def _call_llm(*_args, **_kwargs):
        if kind == "unparseable":
            raise LlmResponseUnparseable()
        return llm.generate_text("probe")

    return _call_llm


def _stub(monkeypatch, target: str, kind: str) -> None:
    import importlib

    module_path, attr = target.rsplit(".", 1)
    monkeypatch.setattr(importlib.import_module(module_path), attr, _call_llm_for(kind))


def _owned_application(monkeypatch):
    app_row = SimpleNamespace(id=uuid.uuid4(), scholarship_id=uuid.uuid4())
    monkeypatch.setattr("app.api.generation.application_repo.get_by_id_for_user", lambda *a, **k: app_row)
    monkeypatch.setattr("app.api.generation.scholarship_repo.get_by_id", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.api.generation.document_repo.list_application_documents_for_application", lambda *a, **k: []
    )
    return app_row


ROUTES = [
    ("import-cv", "/profile/import-cv", "app.api.profile.extract_profile_from_text", "file"),
    ("match-url", "/scholarships/match-url", "app.api.url_match.run_url_match", {"url": "https://example.org/x"}),
    ("match", f"/scholarships/{uuid.uuid4()}/match", "app.api.matching.match_scholarship", None),
    ("qa", f"/scholarships/{uuid.uuid4()}/qa", "app.api.qa.answer_question", {"question": "q"}),
    ("discovery", "/discovery", "app.api.discovery.run_discovery", None),
    ("cv", f"/applications/{uuid.uuid4()}/generate/cv", "app.api.generation.run_cv_gen", None),
    ("sop", f"/applications/{uuid.uuid4()}/generate/sop", "app.api.generation.run_sop_gen", None),
]


@pytest.mark.parametrize("kind", list(KINDS))
@pytest.mark.parametrize("label, path, target, body", ROUTES, ids=[r[0] for r in ROUTES])
def test_each_failure_kind_answers_503_on_each_route(label, path, target, body, kind, authed_user, monkeypatch):
    client, headers = authed_user["client"], authed_user["headers"]
    _install_failure(monkeypatch, kind)
    _stub(monkeypatch, target, kind)
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
    response = client.post(path, **kwargs)

    assert response.status_code == 503, response.text
    payload = response.json()
    assert payload["code"] == "llm_temporarily_unavailable"
    assert payload["reason"] == KINDS[kind]
    assert payload["detail"] == "AI features are temporarily unavailable. Try again after the retry time."
    assert set(payload) == {"detail", "code", "retry_after_seconds", "retry_at", "reason"}
    retry_after = int(response.headers["Retry-After"])
    assert retry_after == payload["retry_after_seconds"] and retry_after >= 1
    for leaked in ("SECRET-PROVIDER-TEXT", "Traceback", "GEMINI_API_KEY"):
        assert leaked not in response.text


def test_retry_time_is_now_plus_the_configured_seconds_when_there_is_no_provider_time(authed_user, monkeypatch):
    _install_failure(monkeypatch, "outage")
    _stub(monkeypatch, "app.api.qa.answer_question", "outage")
    response = authed_user["client"].post(
        f"/scholarships/{uuid.uuid4()}/qa", json={"question": "q"}, headers=authed_user["headers"]
    )
    assert response.status_code == 503
    assert 55 <= response.json()["retry_after_seconds"] <= 61  # llm_unavailable_retry_seconds default 60


def test_a_non_llm_error_is_not_reported_as_an_ai_outage(authed_user, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    llm_gate.install_gate(None)
    monkeypatch.setattr("app.api.qa.answer_question", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    local = TestClient(app, raise_server_exceptions=False)
    response = local.post(f"/scholarships/{uuid.uuid4()}/qa", json={"question": "q"}, headers=authed_user["headers"])
    assert response.status_code == 500
    assert "llm_temporarily_unavailable" not in response.text


def test_not_configured_is_part_of_the_llm_error_family():
    assert issubclass(LlmNotConfiguredError, LlmError)
    assert LlmNotConfiguredError.__module__ == "app.core.llm"  # name and import path unchanged
    assert str(LlmNotConfiguredError("GEMINI_API_KEY is not configured")) == "GEMINI_API_KEY is not configured"


# -- the real parse sites raise LlmResponseUnparseable where a student route reaches them -------------


@pytest.fixture
def garbage_reply(monkeypatch):
    llm_gate.install_gate(AdmittingGate())
    reply = SimpleNamespace(text="this is not json at all")
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda *a, **k: reply))
    monkeypatch.setattr(llm, "get_gemini_client", lambda: client)
    with llm_gate.llm_priority(llm_gate.Priority.USER_FACING):
        yield


def test_extract_profile_raises_unparseable_on_a_garbage_reply(garbage_reply):
    from app.tools.extract_profile import extract_profile_from_text

    with pytest.raises(LlmResponseUnparseable):
        extract_profile_from_text("Jane Doe")


def test_extract_requirements_raises_unparseable_on_a_garbage_reply(garbage_reply):
    from app.tools.extract_requirements import extract_requirements_from_page

    with pytest.raises(LlmResponseUnparseable):
        extract_requirements_from_page("page text", source_url="https://example.org/x")


def test_soft_preference_matching_raises_unparseable_on_a_garbage_reply(garbage_reply):
    from app.agents.matching.agent import produce_soft_preference_outcomes

    from app.models.profile import CriterionKind

    criterion = SimpleNamespace(
        kind=CriterionKind.SOFT_PREFERENCE, dimension="country", operator="eq", value="DE", note=None
    )
    profile = SimpleNamespace(criteria=[criterion])
    scholarship = SimpleNamespace(
        name="s", provider="p", country="DE", field="x", funding_status="full", notes=None, conditions=None, exceptions=None
    )
    with pytest.raises(LlmResponseUnparseable):
        produce_soft_preference_outcomes(profile, scholarship, hard_outcome_criteria=set())


# -- the call-site table (plan.md OQ-1) ---------------------------------------------------------------
# For each module that calls `generate_text` / `embed_texts`: does a failure reach the route or is it
# swallowed? The swallowed ones are named and deliberately left as they are.

REACHES = "reaches_route"
SWALLOWED = "swallowed"

CALL_SITES = {
    "app/agents/matching/agent.py": REACHES,  # match, match-url
    "app/agents/research_qa/agent.py": REACHES,  # qa
    "app/tools/classify.py": SWALLOWED,  # `except Exception` -> rules-only result; deferral queue is T473
    "app/tools/embed.py": REACHES,  # qa retrieval, discovery ingestion
    "app/tools/extract_document_fields.py": REACHES,  # the document pipeline turns it into extraction_unavailable
    "app/tools/extract_listing.py": SWALLOWED,  # discovery step catch / connector (T290) records it, never the route
    "app/tools/extract_profile.py": REACHES,  # import-cv
    "app/tools/extract_requirements.py": REACHES,  # match-url
    "app/workflows/cv_gen/graph.py": REACHES,  # generate/cv (a parse failure stays a 409 gap)
    "app/workflows/sop_gen/graph.py": REACHES,  # generate/sop (a parse failure stays a 409 gap)
}


def _modules_calling_the_provider() -> set[str]:
    root = Path(__file__).resolve().parents[2]
    found = set()
    for path in (root / "app").rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel == "app/core/llm.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"generate_text", "embed_texts"}:
                    found.add(rel)
    return found


def test_call_site_table_lists_every_provider_caller():
    assert _modules_calling_the_provider() == set(CALL_SITES)


def test_classify_swallows_a_provider_failure_and_keeps_the_rules_result(monkeypatch):
    from app.tools import classify as classify_module

    def boom(*_a, **_k):
        raise LlmResponseUnparseable()

    monkeypatch.setattr(classify_module, "_llm_fallback_classify", boom)
    result = classify_module.classify("A scholarship for students")
    assert result.degree_level.value is None  # nothing guessed
