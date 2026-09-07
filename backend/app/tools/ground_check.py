"""`ground_check` tool — deterministic (non-LLM) lexical-overlap check that a
claim is substantiated by retrieved evidence text. Constitution's "harness
discipline" requires grounding checks to be automatic, non-LLM verification
of agent output, not another LLM call trusting itself. Reused by Q&A now
(US3) and by CV/SOP generation in Phase 3."""

import re
from dataclasses import dataclass

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "this",
    "to", "was", "were", "will", "with", "you", "your",
}
_WORD_RE = re.compile(r"[a-z0-9]+")


def _significant_words(text: str) -> set[str]:
    words = _WORD_RE.findall(text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


@dataclass(frozen=True)
class GroundCheckResult:
    grounded: bool
    overlap_score: float
    matched_evidence: str | None


def ground_check(claim: str, evidence_texts: list[str], *, threshold: float = 0.6) -> GroundCheckResult:
    """Returns grounded=True only if some evidence text covers >= `threshold`
    of the claim's significant words. An empty claim or no evidence is never
    grounded — callers must treat that as Unknown, not as a pass."""
    claim_words = _significant_words(claim)
    if not claim_words or not evidence_texts:
        return GroundCheckResult(grounded=False, overlap_score=0.0, matched_evidence=None)

    best_score = 0.0
    best_evidence: str | None = None
    for evidence in evidence_texts:
        evidence_words = _significant_words(evidence)
        if not evidence_words:
            continue
        overlap = len(claim_words & evidence_words) / len(claim_words)
        if overlap > best_score:
            best_score = overlap
            best_evidence = evidence

    return GroundCheckResult(
        grounded=best_score >= threshold,
        overlap_score=best_score,
        matched_evidence=best_evidence if best_score >= threshold else None,
    )
