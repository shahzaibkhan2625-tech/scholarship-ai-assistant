"""RAG embedding pipeline: loaders -> chunk -> embed -> upsert into Qdrant.
Used by url_match (T049) to make a fetched official page retrievable for
Q&A grounding (US3)."""

from datetime import datetime, timezone

from app.data.vectors.qdrant_client import upsert_chunks
from app.rag.chunk import chunk_text
from app.rag.loaders import LoadedDocument, load_html
from app.tools.embed import embed_documents


def ingest_html_page(html: str, *, source_url: str, scholarship_id: str) -> int:
    """Loads, chunks, embeds, and stores one fetched page. Returns the number
    of chunks stored."""
    document = load_html(html, source_url=source_url, scholarship_id=scholarship_id)
    return ingest_document(document)


def ingest_document(document: LoadedDocument) -> int:
    chunks = chunk_text(document.text)
    if not chunks:
        return 0

    vectors = embed_documents(chunks)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    payloads = [
        {
            "scholarship_id": document.scholarship_id,
            "source_url": document.source_url,
            "text": chunk,
            "retrieved_at": retrieved_at,
        }
        for chunk in chunks
    ]
    upsert_chunks(payloads, vectors)
    return len(chunks)
