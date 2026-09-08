"""`rerank` tool (T085, Blueprint §11 "(query, docs) -> ranked docs"; A3:
"Reranker off by default (flag) to control cost/latency"). No HF
cross-encoder dependency is installed (see backend/pyproject.toml) and none
has been onboarded for this slice, so this reuses Phase 1's existing
embedding infrastructure (`tools/embed.py`, the same Gemini embedding model
`rag/retrieve.py` already calls) as the relevance scorer — a cosine-
similarity reranker is the "equivalent reuse of Phase 1's embedding
infrastructure" the task calls for, rather than adding a new ML dependency
for a feature that is off by default.

When disabled (the default), `rerank` is an explicit no-op passthrough:
candidates are returned in exactly the order they were given — never
"close enough" reordering, never a network call.
"""

from dataclasses import dataclass
from typing import Callable

from app.core.config import settings
from app.tools.embed import embed_documents, embed_query

__all__ = ["RerankCandidate", "rerank"]


@dataclass(frozen=True)
class RerankCandidate:
    id: str
    text: str


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def rerank(
    query: str,
    candidates: list[RerankCandidate],
    *,
    enabled: bool | None = None,
    embed_query_fn: Callable[[str], list[float]] = embed_query,
    embed_documents_fn: Callable[[list[str]], list[list[float]]] = embed_documents,
) -> list[RerankCandidate]:
    """Returns `candidates` reordered by relevance to `query`.

    `enabled` defaults to `settings.rerank_enabled` (off, per A3) — pass it
    explicitly only to override the flag for a single call (e.g. tests).
    `embed_query_fn`/`embed_documents_fn` are injectable (mirrors
    `tools/search.py`'s `raw_search` injection) so this is testable without a
    configured Gemini API key.
    """
    use_reranking = settings.rerank_enabled if enabled is None else enabled
    if not use_reranking or len(candidates) < 2:
        return list(candidates)  # explicit no-op passthrough — order is untouched, not just "similar"

    query_vector = embed_query_fn(query)
    doc_vectors = embed_documents_fn([c.text for c in candidates])
    scored = list(zip(candidates, doc_vectors))
    scored.sort(key=lambda pair: _cosine_similarity(query_vector, pair[1]), reverse=True)
    return [candidate for candidate, _ in scored]
