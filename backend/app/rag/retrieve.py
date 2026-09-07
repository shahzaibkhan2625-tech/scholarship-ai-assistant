"""RAG retrieval over Qdrant, scoped to one scholarship's ingested chunks."""

from dataclasses import dataclass

from app.data.vectors.qdrant_client import search_chunks
from app.tools.embed import embed_query


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    source_url: str
    score: float
    retrieved_at: str | None = None


def retrieve(query: str, scholarship_id: str, top_k: int = 5) -> list[RetrievedChunk]:
    query_vector = embed_query(query)
    hits = search_chunks(query_vector, scholarship_id=scholarship_id, top_k=top_k)
    return [
        RetrievedChunk(
            text=hit["text"],
            source_url=hit["source_url"],
            score=hit["score"],
            retrieved_at=hit.get("retrieved_at"),
        )
        for hit in hits
    ]
