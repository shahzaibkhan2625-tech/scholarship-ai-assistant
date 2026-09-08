"""`dedup` service tests (T081, Blueprint §31): deterministic key match
catches exact (name+university+intake) duplicates without touching the
embedding path; near-duplicates are only caught via the (mocked)
vector-similarity path, and are always flagged for review, never
silently auto-merged."""

from unittest.mock import MagicMock

from app.services.dedup import DedupCandidate, DedupDecision, find_duplicate


def test_identical_key_is_caught_deterministically_without_embedding_call():
    existing = [DedupCandidate(id="existing-1", name="DAAD EPOS", university="TU Berlin", intake="Fall 2026")]
    candidate = DedupCandidate(id="new-1", name="DAAD EPOS", university="TU Berlin", intake="Fall 2026")

    embed_mock = MagicMock(side_effect=AssertionError("embedding should not be called for a deterministic match"))

    result = find_duplicate(candidate, existing, embed=embed_mock)

    assert result.decision == DedupDecision.MERGE_WITH_EXISTING
    assert result.matched_id == "existing-1"
    embed_mock.assert_not_called()


def test_near_duplicate_caught_by_vector_similarity_is_flagged_not_merged():
    existing = [
        DedupCandidate(
            id="existing-2",
            name="DAAD Scholarship Programme",
            university="TU Munich",
            intake="Spring 2027",
            text="DAAD Scholarship Programme for international graduate students",
        )
    ]
    candidate = DedupCandidate(
        id="new-2",
        name="DAAD Scholarship Program",  # not an exact key match
        university="Technical University Munich",
        intake="Spring 2027 intake",
        text="DAAD Scholarship Program for international graduate students",
    )

    def fake_embed(text: str) -> list[float]:
        # Both texts embed to (near-)identical vectors -> high cosine similarity.
        return [1.0, 0.0, 0.0] if "Programme" in text else [0.99, 0.01, 0.0]

    result = find_duplicate(candidate, existing, embed=fake_embed)

    assert result.decision == DedupDecision.FLAG_FOR_REVIEW
    assert result.matched_id == "existing-2"
    assert result.similarity is not None and result.similarity >= 0.90


def test_dissimilar_record_is_new():
    existing = [
        DedupCandidate(id="existing-3", name="KAUST Fellowship", text="KAUST Fellowship for engineering PhD students")
    ]
    candidate = DedupCandidate(
        id="new-3", name="Chevening Scholarship", text="Chevening Scholarship for UK master's programs"
    )

    def fake_embed(text: str) -> list[float]:
        return [1.0, 0.0] if "KAUST" in text else [0.0, 1.0]

    result = find_duplicate(candidate, existing, embed=fake_embed)

    assert result.decision == DedupDecision.NEW
