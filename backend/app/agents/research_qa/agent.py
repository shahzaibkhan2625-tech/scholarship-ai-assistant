"""Research/Q&A agent — grounded via retrieve (rag/retrieve.py) + a
deterministic, non-LLM grounding check (tools/ground_check.py). Every answer
is labeled Verified/Inferred/Unknown; when information cannot be verified,
the agent says so explicitly rather than guessing (constitution Principle I,
PRD FR-QA-3). Only cites content retrieved from ingested official-source
pages — never invents an answer from outside knowledge."""

from app.rag.retrieve import retrieve
from app.schemas.common import Confidence
from app.schemas.qa import QaAnswer
from app.tools.ground_check import ground_check

_VERIFIED_THRESHOLD = 0.6
_INFERRED_THRESHOLD = 0.3
_COULD_NOT_CONFIRM = "I could not confirm this from the official scholarship content."

_SYSTEM_INSTRUCTION = f"""You answer a student's question about a specific scholarship using ONLY
the provided official-source excerpts. If the excerpts do not state the answer, respond with
EXACTLY this sentence and nothing else: "{_COULD_NOT_CONFIRM}"
Do not use outside knowledge, do not guess, and never hedge with words like "likely" or
"probably" as if they were verified facts — if you are not certain, say you could not confirm it."""


def _unknown_answer() -> QaAnswer:
    return QaAnswer(answer_text=_COULD_NOT_CONFIRM, confidence=Confidence.UNKNOWN)


def answer_question(question: str, scholarship_id: str, top_k: int = 5) -> QaAnswer:
    chunks = retrieve(question, scholarship_id, top_k=top_k)
    if not chunks:
        return _unknown_answer()

    from app.core.llm import generate_text

    context = "\n\n".join(f"[Source: {c.source_url}]\n{c.text}" for c in chunks)
    prompt = f"Question: {question}\n\nOfficial-source excerpts:\n{context}"
    answer_text = generate_text(prompt, system_instruction=_SYSTEM_INSTRUCTION).strip()

    if not answer_text or _COULD_NOT_CONFIRM.lower() in answer_text.lower():
        return _unknown_answer()

    chunk_texts = [c.text for c in chunks]

    verified_check = ground_check(answer_text, chunk_texts, threshold=_VERIFIED_THRESHOLD)
    if verified_check.grounded:
        return _labeled_answer(answer_text, Confidence.VERIFIED, verified_check.matched_evidence, chunks)

    inferred_check = ground_check(answer_text, chunk_texts, threshold=_INFERRED_THRESHOLD)
    if inferred_check.grounded:
        return _labeled_answer(answer_text, Confidence.INFERRED, inferred_check.matched_evidence, chunks)

    # The LLM produced an answer the deterministic grounding check cannot
    # substantiate against retrieved content — never surface it as fact.
    return _unknown_answer()


def _labeled_answer(answer_text: str, confidence: Confidence, evidence: str | None, chunks) -> QaAnswer:
    matched_chunk = next((c for c in chunks if c.text == evidence), chunks[0])
    return QaAnswer(
        answer_text=answer_text,
        confidence=confidence,
        source_url=matched_chunk.source_url,
        last_verified_at=matched_chunk.retrieved_at,
        evidence_snippet=evidence,
    )
