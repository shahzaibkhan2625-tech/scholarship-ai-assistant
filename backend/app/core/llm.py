"""Thin wrapper around the Gemini API (free-tier) used for text generation and
embeddings. All LLM access in this codebase goes through here so the model
names and credential handling live in exactly one place.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar
from zoneinfo import ZoneInfo

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import settings
from app.core.llm_errors import classify_provider_error
from app.core.llm_gate import LlmError, RequestType, get_gate, require_priority

_T = TypeVar("_T")

EMBEDDING_DIMENSIONS = 768  # fixed: must match the stored vectors; the model names are settings (FR-BUDGET-12)

_client: genai.Client | None = None


class LlmNotConfiguredError(LlmError, RuntimeError):
    """Raised when GEMINI_API_KEY is not set. Callers must handle this
    explicitly (e.g. surface an Unknown/could-not-confirm result) rather than
    letting an unconfigured LLM silently produce no output. Part of the `LlmError` family (003, FR-ERR-6):
    a student route answers 503 `not_configured`, a monitoring check records a provider outcome."""

    reason = "not_configured"
    fetch_outcome = "provider_error"


def get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise LlmNotConfiguredError("GEMINI_API_KEY is not configured")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _translate(exc: BaseException) -> LlmError:
    return classify_provider_error(
        exc,
        now=datetime.now(timezone.utc),
        tz=ZoneInfo(settings.llm_budget_timezone),
        default_pause_seconds=settings.llm_default_pause_seconds,
    )


def _call(request_type: RequestType, operation: Callable[[genai.Client], _T]) -> _T:
    """The single choke point for provider calls (003, FR-BUDGET-1): a priority class is required, the
    gate admits or refuses before the call, and a provider error is translated and reported after it.
    Exactly one client call per operation: there is no retry here and none in the client."""
    priority = require_priority()
    client = get_gemini_client()  # raises LlmNotConfiguredError before any budget is taken
    gate = get_gate()
    gate.acquire(request_type, priority)
    try:
        return operation(client)
    except (genai_errors.APIError, OSError, httpx.HTTPError) as exc:
        error = _translate(exc)
        gate.report_provider_error(request_type, error)
        raise error from None


def generate_text(prompt: str, *, system_instruction: str | None = None, temperature: float | None = None) -> str:
    config = (
        types.GenerateContentConfig(system_instruction=system_instruction, temperature=temperature)
        if system_instruction or temperature is not None
        else None
    )

    def operation(client: genai.Client) -> str:
        response = client.models.generate_content(model=settings.llm_generation_model, contents=prompt, config=config)
        return response.text or ""

    return _call(RequestType.GENERATE, operation)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    def operation(client: genai.Client) -> list[list[float]]:
        response = client.models.embed_content(
            model=settings.llm_embedding_model,
            contents=texts,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
        )
        return [list(e.values) for e in response.embeddings]

    return _call(RequestType.EMBED, operation)
