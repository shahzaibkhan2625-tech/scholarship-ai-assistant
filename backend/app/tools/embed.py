"""`embed` tool — typed wrapper over the Gemini embedding model for RAG
ingestion and query embedding. External-call surface only (plan.md tools/
convention); no business logic here."""

from app.core.llm import embed_texts


def embed_query(query: str) -> list[float]:
    return embed_texts([query])[0]


def embed_documents(texts: list[str]) -> list[list[float]]:
    return embed_texts(texts)
