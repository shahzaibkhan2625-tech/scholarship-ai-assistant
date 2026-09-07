"""RAG grounding-check node: retrieve + ground_check combined into a single
grounding decision consumed by Q&A (US3) and, later, CV/SOP generation."""

from dataclasses import dataclass

from app.rag.retrieve import RetrievedChunk, retrieve
from app.schemas.common import Confidence
from app.tools.ground_check import ground_check


@dataclass(frozen=True)
class GroundingResult:
    confidence: Confidence
    evidence_snippet: str | None
    source_url: str | None
    last_verified_at: str | None
    retrieved_chunks: list[RetrievedChunk]


def check_grounding(claim: str, scholarship_id: str, top_k: int = 5) -> GroundingResult:
    """Retrieves the scholarship's ingested chunks and checks whether `claim`
    is grounded in them. Never guesses: no matching evidence => Unknown."""
    chunks = retrieve(claim, scholarship_id, top_k=top_k)
    if not chunks:
        return GroundingResult(
            confidence=Confidence.UNKNOWN,
            evidence_snippet=None,
            source_url=None,
            last_verified_at=None,
            retrieved_chunks=[],
        )

    result = ground_check(claim, [c.text for c in chunks])
    if not result.grounded:
        return GroundingResult(
            confidence=Confidence.UNKNOWN,
            evidence_snippet=None,
            source_url=None,
            last_verified_at=None,
            retrieved_chunks=chunks,
        )

    matched_chunk = next((c for c in chunks if c.text == result.matched_evidence), chunks[0])
    return GroundingResult(
        confidence=Confidence.VERIFIED,
        evidence_snippet=result.matched_evidence,
        source_url=matched_chunk.source_url,
        last_verified_at=matched_chunk.retrieved_at,
        retrieved_chunks=chunks,
    )
