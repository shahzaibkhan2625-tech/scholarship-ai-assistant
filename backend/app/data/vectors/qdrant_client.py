"""Qdrant Cloud client wrapper. Qdrant is retrieval knowledge only — never the
source of truth for fresh, time-sensitive facts (constitution: fresh facts
live in Postgres and are re-verified live against the official source when
stale).
"""

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from app.core.config import settings
from app.core.llm import EMBEDDING_DIMENSIONS

SCHOLARSHIP_CHUNKS_COLLECTION = "scholarship_chunks"

_client: QdrantClient | None = None


class VectorStoreNotConfiguredError(RuntimeError):
    """Raised when QDRANT_URL/QDRANT_API_KEY are not set."""


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        if not settings.qdrant_url or not settings.qdrant_api_key:
            raise VectorStoreNotConfiguredError("QDRANT_URL/QDRANT_API_KEY are not configured")
        _client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    return _client


def ensure_collection(collection_name: str = SCHOLARSHIP_CHUNKS_COLLECTION) -> None:
    client = get_qdrant_client()
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
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
    client.upsert(collection_name=collection_name, points=points)
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
