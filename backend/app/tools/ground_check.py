"""`ground_check` tool — deterministic (non-LLM) lexical-overlap check that a
claim is substantiated by retrieved evidence text. Constitution's "harness
discipline" requires grounding checks to be automatic, non-LLM verification
of agent output, not another LLM call trusting itself. Currently used by Q&A
(US3, `app/rag/grounding.py`) only — CV/SOP generation (Phase 3) calls
`verify_claim_grounded` below instead; see its docstring and
`ground_check()`'s own for why."""

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
    grounded — callers must treat that as Unknown, not as a pass.

    **Known limitation — do not use this for generation grounding.** The
    `_significant_words` filter drops short tokens (`len(w) >= 3`), which
    incidentally drops single/double-digit numbers ("2", "5"). That means a
    claim that *overstates* a real number ("5 years" when the source says "2
    years") can pass on lexical overlap of the surrounding words alone —
    proven by
    `tests/tools/test_ground_check.py::test_bare_ground_check_overlap_alone_does_not_catch_numeric_overstatement`.
    This function's behaviour is intentionally left unchanged here (its only
    current caller, `app/rag/grounding.py`'s Q&A grounding, is untouched by
    that decision). Any new caller doing generation-style grounding (CV/SOP
    and similar) MUST use `verify_claim_grounded` below instead, which layers
    a numeric-consistency check on top."""
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


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def verify_claim_grounded(claim: str, evidence_texts: list[str], *, threshold: float = 0.6) -> GroundCheckResult:
    """Stricter grounding gate, layered on top of `ground_check` rather than
    modifying it, for callers (CV/SOP generation) where a claim that
    *overstates* a real number must be rejected, not passed. `ground_check`'s
    significant-word filter drops short numeric tokens (`len(w) >= 3` excludes
    single/double-digit numbers like "2" or "5"), so lexical overlap of the
    surrounding words alone cannot tell "5 years of experience" apart from a
    source that says "2 years of experience" — both overlap 100% on
    {"years", "experience"}. This function additionally requires every number
    literal in the claim to appear in the matched evidence text. Every
    existing `ground_check` caller (`app.rag.grounding`) is untouched."""
    result = ground_check(claim, evidence_texts, threshold=threshold)
    if not result.grounded:
        return result

    claim_numbers = set(_NUMBER_RE.findall(claim))
    evidence_numbers = set(_NUMBER_RE.findall(result.matched_evidence or ""))
    if claim_numbers and not claim_numbers.issubset(evidence_numbers):
        return GroundCheckResult(grounded=False, overlap_score=result.overlap_score, matched_evidence=None)
    return result
