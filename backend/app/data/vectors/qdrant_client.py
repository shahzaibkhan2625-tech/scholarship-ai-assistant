"""Qdrant Cloud client wrapper. Qdrant is retrieval knowledge only — never the
source of truth for fresh, time-sensitive facts (constitution: fresh facts
live in Postgres and are re-verified live against the official source when
stale).
"""

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.core.config import settings
from app.core.llm import EMBEDDING_DIMENSIONS

SCHOLARSHIP_CHUNKS_COLLECTION = "scholarship_chunks"

# Default timeout (seconds) for all client calls except the bulk chunk-upsert
# below, which needs more headroom (see UPSERT_TIMEOUT_SECONDS). Qdrant's
# client defaults to httpx's ~5s timeout when unset, which is too tight even
# for interactive calls against a free-tier cluster.
DEFAULT_TIMEOUT_SECONDS = 30
# Bulk chunk-upsert during RAG ingestion is the slow path: a freshly-resumed
# free-tier Qdrant Cloud cluster can take tens of seconds to wake up, on top
# of the write itself. 60s covers that without being unbounded.
UPSERT_TIMEOUT_SECONDS = 60

SCHOLARSHIP_ID_PAYLOAD_FIELD = "scholarship_id"

_client: QdrantClient | None = None


class VectorStoreNotConfiguredError(RuntimeError):
    """Raised when QDRANT_URL/QDRANT_API_KEY are not set."""


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        if not settings.qdrant_url or not settings.qdrant_api_key:
            raise VectorStoreNotConfiguredError("QDRANT_URL/QDRANT_API_KEY are not configured")
        _client = QdrantClient(
            url=settings.qdrant_url, api_key=settings.qdrant_api_key, timeout=DEFAULT_TIMEOUT_SECONDS
        )
    return _client


def ensure_collection(collection_name: str = SCHOLARSHIP_CHUNKS_COLLECTION) -> None:
    """Idempotent: safe to call every time (it already is, from
    `upsert_chunks`). Self-heals collections created before the
    `scholarship_id` payload index existed, not just newly-created ones —
    `query_points`'s filter on that field 400s without it."""
    client = get_qdrant_client()
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
        )

    existing_schema = client.get_collection(collection_name).payload_schema
    if SCHOLARSHIP_ID_PAYLOAD_FIELD not in existing_schema:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=SCHOLARSHIP_ID_PAYLOAD_FIELD,
            field_schema=PayloadSchemaType.KEYWORD,
        )


def upsert_chunks(
    chunks: list[dict],
    vectors: list[list[float]],
    collection_name: str = SCHOLARSHIP_CHUNKS_COLLECTION,
) -> list[str]:
    """Each entry in `chunks` is a payload dict (must include `scholarship_id`,
    `text`, `source_url`); `vectors` is the parallel list of embeddings."""
    ensure_collection(collection_name)
    client = get_qdrant_client()
    ids = [str(uuid.uuid4()) for _ in chunks]
    points = [
        PointStruct(id=point_id, vector=vector, payload=payload)
        for point_id, vector, payload in zip(ids, vectors, chunks, strict=True)
    ]
    client.upsert(collection_name=collection_name, points=points, timeout=UPSERT_TIMEOUT_SECONDS)
    return ids


def search_chunks(
    query_vector: list[float],
    scholarship_id: str,
    top_k: int = 5,
    collection_name: str = SCHOLARSHIP_CHUNKS_COLLECTION,
) -> list[dict]:
    client = get_qdrant_client()
    if not client.collection_exists(collection_name):
        return []
    results = client.query_points(
        collection_name=collection_name,
        query=query_vector,
        query_filter=Filter(
            must=[FieldCondition(key="scholarship_id", match=MatchValue(value=scholarship_id))]
        ),
        limit=top_k,
    ).points
    return [{"score": r.score, **(r.payload or {})} for r in results]
