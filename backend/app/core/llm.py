"""Thin wrapper around the Gemini API (free-tier) used for text generation and
embeddings. All LLM access in this codebase goes through here so the model
names and credential handling live in exactly one place.
"""

from google import genai
from google.genai import types

from app.core.config import settings

GENERATION_MODEL = "models/gemini-2.5-flash"
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768

_client: genai.Client | None = None


class LlmNotConfiguredError(RuntimeError):
    """Raised when GEMINI_API_KEY is not set. Callers must handle this
    explicitly (e.g. surface an Unknown/could-not-confirm result) rather than
    letting an unconfigured LLM silently produce no output."""


def get_gemini_client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise LlmNotConfiguredError("GEMINI_API_KEY is not configured")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def generate_text(prompt: str, *, system_instruction: str | None = None) -> str:
    client = get_gemini_client()
    config = types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None
    response = client.models.generate_content(
        model=GENERATION_MODEL,
        contents=prompt,
        config=config,
    )
    return response.text or ""


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = get_gemini_client()
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
    )
    return [list(e.values) for e in response.embeddings]
