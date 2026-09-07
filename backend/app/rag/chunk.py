"""Deterministic word-window chunker for RAG ingestion. No LLM involved."""

DEFAULT_CHUNK_SIZE_WORDS = 200
DEFAULT_OVERLAP_WORDS = 40


def chunk_text(
    text: str,
    *,
    chunk_size_words: int = DEFAULT_CHUNK_SIZE_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
) -> list[str]:
    if chunk_size_words <= 0:
        raise ValueError("chunk_size_words must be positive")
    if overlap_words < 0 or overlap_words >= chunk_size_words:
        raise ValueError("overlap_words must be >= 0 and smaller than chunk_size_words")

    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    step = chunk_size_words - overlap_words
    while start < len(words):
        chunk_words = words[start : start + chunk_size_words]
        chunks.append(" ".join(chunk_words))
        if start + chunk_size_words >= len(words):
            break
        start += step
    return chunks
