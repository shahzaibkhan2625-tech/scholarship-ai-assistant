"""`dedup` service (T081, Blueprint §31) — deterministic key match
(name+university+intake) first, then a vector-similarity threshold check for
near-duplicates, reusing the existing Gemini embedding tool
(`app/tools/embed.py`) rather than a second embedding path. Returns an
explicit decision — `new` / `merge_with_existing` / `flag_for_review` — and
never silently auto-merges: an exact deterministic key match still comes
back as a `merge_with_existing` *decision* for the caller to apply (it does
not overwrite anything itself), and a near-duplicate caught only by vector
similarity is always `flag_for_review`, never merged outright, so a
stricter source's data can never be silently lost.
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from app.tools.embed import embed_query

__all__ = ["DedupDecision", "DedupCandidate", "DedupResult", "find_duplicate"]

_DEFAULT_SIMILARITY_THRESHOLD = 0.90


class DedupDecision(StrEnum):
    NEW = "new"
    MERGE_WITH_EXISTING = "merge_with_existing"
    FLAG_FOR_REVIEW = "flag_for_review"


@dataclass(frozen=True)
class DedupCandidate:
    id: str
    name: str
    university: str | None = None
    intake: str | None = None
    text: str | None = None  # descriptive text used for the vector-similarity fallback


@dataclass(frozen=True)
class DedupResult:
    decision: DedupDecision
    matched_id: str | None = None
    similarity: float | None = None
    reason: str = ""


def _dedup_key(candidate: DedupCandidate) -> tuple[str, str, str]:
    return (
        (candidate.name or "").strip().lower(),
        (candidate.university or "").strip().lower(),
        (candidate.intake or "").strip().lower(),
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_duplicate(
    candidate: DedupCandidate,
    existing: list[DedupCandidate],
    *,
    similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
    embed: Callable[[str], list[float]] = embed_query,
) -> DedupResult:
    candidate_key = _dedup_key(candidate)
    if all(candidate_key):
        for row in existing:
            if _dedup_key(row) == candidate_key:
                return DedupResult(
                    decision=DedupDecision.MERGE_WITH_EXISTING,
                    matched_id=row.id,
                    reason="deterministic key match (name+university+intake)",
                )

    comparable_existing = [row for row in existing if row.text]
    if candidate.text is None or not comparable_existing:
        return DedupResult(decision=DedupDecision.NEW, reason="no deterministic match; nothing to compare by similarity")

    candidate_vector = embed(candidate.text)
    best_id: str | None = None
    best_score = 0.0
    for row in comparable_existing:
        score = _cosine_similarity(candidate_vector, embed(row.text))
        if score > best_score:
            best_score = score
            best_id = row.id

    if best_id is not None and best_score >= similarity_threshold:
        return DedupResult(
            decision=DedupDecision.FLAG_FOR_REVIEW,
            matched_id=best_id,
            similarity=best_score,
            reason="near-duplicate by vector similarity; flagged for human review, not auto-merged",
        )

    return DedupResult(decision=DedupDecision.NEW, similarity=best_score if best_id else None)
