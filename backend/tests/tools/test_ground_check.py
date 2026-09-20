"""`ground_check` tool tests (T097). Proves the grounding gate BLOCKS
generation on an untraceable claim rather than silently accepting it.

`ground_check` itself (the base lexical-overlap function, reused unchanged
from Q&A/US3 — T019/T020) is deterministic and non-LLM by design (its own
docstring: "not another LLM call trusting itself"). Its `_significant_words`
filter drops short tokens (`len(w) >= 3`), which incidentally drops
single/double-digit numbers ("2", "5") — so a claim that *overstates* a real
number (sources say 2 years, claim says 5) can pass on lexical overlap of the
surrounding words alone. Rather than weakening `ground_check`'s existing
behaviour (and risking Q&A's grounding getting looser), the caller is
adapted: `verify_claim_grounded` layers a numeric-consistency check on top,
and this is what CV/SOP generation (T109/T110) actually calls."""

from app.tools.ground_check import ground_check, verify_claim_grounded

_EVIDENCE = [
    "The applicant completed a Bachelor of Science in Computer Science at MIT.",
    "The applicant has 2 years of professional software engineering experience.",
]


def test_claim_fully_supported_by_source_passes() -> None:
    claim = "Completed a Bachelor of Science in Computer Science at MIT."

    result = ground_check(claim, _EVIDENCE)

    assert result.grounded is True
    assert result.matched_evidence == _EVIDENCE[0]


def test_claim_with_no_supporting_source_is_rejected() -> None:
    claim = "Won the Nobel Prize in Physics."

    result = ground_check(claim, _EVIDENCE)

    assert result.grounded is False
    assert result.matched_evidence is None


def test_claim_with_no_evidence_at_all_is_rejected_not_assumed_true() -> None:
    result = ground_check("Completed a Bachelor of Science in Computer Science at MIT.", [])

    assert result.grounded is False


def test_bare_ground_check_overlap_alone_does_not_catch_numeric_overstatement() -> None:
    """Documents the exact gap `verify_claim_grounded` exists to close: pure
    lexical overlap on the surrounding words is high enough to pass even
    though the number itself has been changed from 2 to 5."""
    overstated_claim = "Has 5 years of professional software engineering experience."

    result = ground_check(overstated_claim, _EVIDENCE)

    assert result.grounded is True  # the gap this test documents


def test_claim_that_overstates_a_real_fact_is_rejected_by_verify_claim_grounded() -> None:
    """Sources say 2 years of experience; the claim says 5. This MUST be
    rejected, not silently accepted — this is the caller CV/SOP generation
    actually uses, not the bare `ground_check` overlap check."""
    overstated_claim = "Has 5 years of professional software engineering experience."

    result = verify_claim_grounded(overstated_claim, _EVIDENCE)

    assert result.grounded is False
    assert result.matched_evidence is None


def test_verify_claim_grounded_still_passes_a_claim_with_matching_numbers() -> None:
    accurate_claim = "Has 2 years of professional software engineering experience."

    result = verify_claim_grounded(accurate_claim, _EVIDENCE)

    assert result.grounded is True
    assert result.matched_evidence == _EVIDENCE[1]


def test_verify_claim_grounded_still_rejects_an_unsupported_claim() -> None:
    result = verify_claim_grounded("Won the Nobel Prize in Physics.", _EVIDENCE)

    assert result.grounded is False
