"""Live-service liveness checks for Qdrant Cloud and Gemini (T138).

Unlike the rest of the suite, these tests deliberately call the REAL
configured services instead of mocking the LLM/vector-store boundary. They
exist because T130's manual verification found Qdrant Cloud's free-tier
cluster had auto-suspended and no automated test caught it. These are
liveness checks, not functional/quality checks (see backend/evals/ for
those) - they only prove the configured services are reachable and
answering.

Excluded from the normal suite via the `smoke` marker (see
pyproject.toml's `addopts = "-m 'not smoke'"`); run explicitly with
`uv run pytest -m smoke backend/tests/smoke/`, as deep-ci.yml does weekly.
"""

import pytest

from app.core.llm import EMBEDDING_DIMENSIONS, embed_texts, generate_text
from app.data.vectors.qdrant_client import ensure_collection, get_qdrant_client, search_chunks

pytestmark = pytest.mark.smoke


def test_qdrant_reachable():
    """Read-only: lists collections to prove the cluster is up and the
    configured credentials are accepted. Does not touch any collection's
    data."""
    client = get_qdrant_client()
    response = client.get_collections()
    assert hasattr(response, "collections")


def test_qdrant_collection_query_works():
    """Exercises a real read against the live scholarship_chunks
    collection using a scholarship_id that will never match a real
    scholarship, so it returns an empty result without writing any point
    data.

    Note: `ensure_collection()` is not strictly read-only in all cases -
    it self-heals by creating the collection (and the scholarship_id
    payload index) if either is missing, per its own docstring. Against
    today's live cluster, where both already exist, this is a no-op read;
    against a freshly reset cluster it would create them. That's the
    intended self-healing behaviour, not a side effect this test needs to
    clean up - collection/index creation is idempotent infrastructure
    state, not test data.
    """
    ensure_collection()
    results = search_chunks(
        query_vector=[0.0] * EMBEDDING_DIMENSIONS,
        scholarship_id="__smoke_test_never_matches__",
    )
    assert results == []


def test_gemini_generation_reachable():
    """One real generation call - proves the model is reachable, the API
    key is valid, and the model name hasn't been deprecated/retired."""
    response = generate_text("Reply with exactly one word: OK")
    assert isinstance(response, str)
    assert response.strip() != ""


def test_gemini_embedding_reachable():
    """One real embedding call - cheapest Gemini endpoint - proves the
    embedding model is reachable and returns vectors of the expected
    dimensionality."""
    vectors = embed_texts(["smoke test"])
    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSIONS
