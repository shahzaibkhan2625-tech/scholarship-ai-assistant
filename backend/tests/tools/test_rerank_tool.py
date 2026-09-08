"""`rerank` tool tests (T085, Blueprint §11; A3 "reranker off by default").
When disabled (the default), `rerank` must be a proven no-op — output order
identical to input order, not merely "close enough" — and it must never call
the embedding functions at all. When explicitly enabled, it must actually
reorder a fixture where relevance clearly differs. All embedding calls are
mocked (no real Gemini API key needed).
"""

from unittest.mock import Mock

from app.core.config import settings
from app.tools.rerank import RerankCandidate, rerank

_CANDIDATES = [
    RerankCandidate(id="a", text="Scholarship in mechanical engineering, Germany"),
    RerankCandidate(id="b", text="Scholarship in computer science, Germany"),
    RerankCandidate(id="c", text="Scholarship in fine arts, France"),
]


def test_disabled_by_default():
    assert settings.rerank_enabled is False


def test_reranking_disabled_returns_input_order_unchanged_and_never_calls_embeddings():
    embed_query_fn = Mock(side_effect=AssertionError("must not be called when disabled"))
    embed_documents_fn = Mock(side_effect=AssertionError("must not be called when disabled"))

    result = rerank(
        "computer science scholarships",
        _CANDIDATES,
        enabled=False,
        embed_query_fn=embed_query_fn,
        embed_documents_fn=embed_documents_fn,
    )

    assert [c.id for c in result] == [c.id for c in _CANDIDATES]
    assert result is not _CANDIDATES  # returns a new list, but with identical order/contents
    embed_query_fn.assert_not_called()
    embed_documents_fn.assert_not_called()


def test_reranking_disabled_uses_settings_flag_when_enabled_not_passed():
    embed_query_fn = Mock(side_effect=AssertionError("must not be called when disabled via settings default"))
    embed_documents_fn = Mock(side_effect=AssertionError("must not be called when disabled via settings default"))

    result = rerank(
        "computer science scholarships",
        _CANDIDATES,
        embed_query_fn=embed_query_fn,
        embed_documents_fn=embed_documents_fn,
    )

    assert [c.id for c in result] == [c.id for c in _CANDIDATES]


def test_reranking_enabled_actually_changes_order_when_it_should():
    # Query vector is closest to candidate "b" (computer science), then "a",
    # then "c" — cosine similarity should promote "b" to the front even
    # though it was ranked second in the input.
    query_vector = [1.0, 0.0, 0.0]
    doc_vectors = {
        "a": [0.0, 1.0, 0.0],  # orthogonal -> similarity 0
        "b": [0.9, 0.1, 0.0],  # near-parallel -> highest similarity
        "c": [0.0, 0.0, 1.0],  # orthogonal -> similarity 0
    }

    def fake_embed_documents(texts: list[str]) -> list[list[float]]:
        # _CANDIDATES order is a, b, c — map positionally.
        order = [c.id for c in _CANDIDATES]
        return [doc_vectors[cid] for cid in order]

    result = rerank(
        "computer science scholarships",
        _CANDIDATES,
        enabled=True,
        embed_query_fn=lambda _q: query_vector,
        embed_documents_fn=fake_embed_documents,
    )

    assert [c.id for c in result] != [c.id for c in _CANDIDATES]
    assert result[0].id == "b"


def test_single_candidate_is_always_a_passthrough_even_when_enabled():
    embed_query_fn = Mock(side_effect=AssertionError("must not be called for a single candidate"))
    embed_documents_fn = Mock(side_effect=AssertionError("must not be called for a single candidate"))

    result = rerank(
        "query",
        [_CANDIDATES[0]],
        enabled=True,
        embed_query_fn=embed_query_fn,
        embed_documents_fn=embed_documents_fn,
    )

    assert [c.id for c in result] == ["a"]
