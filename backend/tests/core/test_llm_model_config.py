"""003 T457 (FR-BUDGET-12, audit B-55): the two model names come from settings, with today's values as defaults;
the embedding dimension stays 768 and is not a setting. Fake client records the model; no network."""

from types import SimpleNamespace

import pytest

from app.core import llm, llm_gate
from app.core.config import Settings
from app.core.llm_gate import Priority, llm_priority


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@localhost/db")  # allow-secret
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-0123456789abcdef-0123456789")  # allow-secret
    for name in ("LLM_GENERATION_MODEL", "LLM_EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_are_todays_model_names(env):
    s = Settings(_env_file=None)
    assert s.llm_generation_model == "models/gemini-2.5-flash"
    assert s.llm_embedding_model == "models/gemini-embedding-001"


def test_the_names_are_read_from_the_environment(env):
    env.setenv("LLM_GENERATION_MODEL", "models/other-generation")
    env.setenv("LLM_EMBEDDING_MODEL", "models/other-embedding")
    s = Settings(_env_file=None)
    assert (s.llm_generation_model, s.llm_embedding_model) == ("models/other-generation", "models/other-embedding")


def test_the_embedding_dimension_is_fixed_and_not_a_setting(env):
    env.setenv("EMBEDDING_DIMENSIONS", "1024")
    s = Settings(_env_file=None)
    assert llm.EMBEDDING_DIMENSIONS == 768
    assert not any("dimension" in field.lower() for field in Settings.model_fields)
    assert not hasattr(s, "embedding_dimensions")


class _Gate:
    def acquire(self, request_type, priority):
        return None

    def report_provider_error(self, request_type, error):
        return None


@pytest.fixture
def recorder(monkeypatch):
    seen = {}

    class Models:
        def generate_content(self, **kwargs):
            seen["generate"] = kwargs["model"]
            return SimpleNamespace(text="ok")

        def embed_content(self, **kwargs):
            seen["embed"] = kwargs["model"]
            seen["dimensions"] = kwargs["config"].output_dimensionality
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1])])

    monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=Models()))
    llm_gate.install_gate(_Gate())
    yield seen
    llm_gate.install_gate(None)


def test_the_client_gets_the_default_names(recorder):
    with llm_priority(Priority.USER_FACING):
        llm.generate_text("x")
        llm.embed_texts(["x"])
    assert recorder == {"generate": "models/gemini-2.5-flash", "embed": "models/gemini-embedding-001", "dimensions": 768}


def test_changing_the_settings_changes_the_model_passed_to_the_client(recorder, monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_generation_model", "models/g-test")
    monkeypatch.setattr(llm.settings, "llm_embedding_model", "models/e-test")
    with llm_priority(Priority.USER_FACING):
        llm.generate_text("x")
        llm.embed_texts(["x"])
    assert (recorder["generate"], recorder["embed"], recorder["dimensions"]) == ("models/g-test", "models/e-test", 768)
