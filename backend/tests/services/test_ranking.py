"""Unit test: ranking never reorders past a hard-constraint failure —
deterministic ranking formula, not agent-controlled."""

import uuid

from app.schemas.match import CriterionOutcome, EligibilityVerdict, MatchStrength, MatchVerdict
from app.services.ranking import rank_matches


def _verdict(eligibility: EligibilityVerdict, strength: MatchStrength, hard_fail: bool = False) -> MatchVerdict:
    hard_constraints = (
        [CriterionOutcome(criterion="nationality", result="fail", evidence="mismatch")] if hard_fail else []
    )
    return MatchVerdict(
        scholarship_id=uuid.uuid4(),
        eligibility_verdict=eligibility,
        match_strength=strength,
        hard_constraints=hard_constraints,
    )


def test_not_verdict_always_ranks_last_even_with_strong_match_strength() -> None:
    strong_but_ineligible = _verdict(EligibilityVerdict.NOT, MatchStrength.STRONG, hard_fail=True)
    weak_but_eligible = _verdict(EligibilityVerdict.POSSIBLY, MatchStrength.POSSIBLE)

    ranked = rank_matches([strong_but_ineligible, weak_but_eligible])

    assert ranked[0] is weak_but_eligible
    assert ranked[1] is strong_but_ineligible


def test_eligible_ranks_before_likely_before_possibly() -> None:
    eligible = _verdict(EligibilityVerdict.ELIGIBLE, MatchStrength.STRONG)
    likely = _verdict(EligibilityVerdict.LIKELY, MatchStrength.POSSIBLE)
    possibly = _verdict(EligibilityVerdict.POSSIBLY, MatchStrength.POSSIBLE)

    ranked = rank_matches([possibly, eligible, likely])

    assert ranked == [eligible, likely, possibly]


def test_within_same_verdict_strong_strength_ranks_before_possible() -> None:
    strong = _verdict(EligibilityVerdict.LIKELY, MatchStrength.STRONG)
    possible = _verdict(EligibilityVerdict.LIKELY, MatchStrength.POSSIBLE)

    ranked = rank_matches([possible, strong])

    assert ranked == [strong, possible]


def test_multiple_hard_failures_all_stay_ranked_last_regardless_of_order() -> None:
    ineligible_1 = _verdict(EligibilityVerdict.NOT, MatchStrength.STRONG, hard_fail=True)
    ineligible_2 = _verdict(EligibilityVerdict.NOT, MatchStrength.STRONG, hard_fail=True)
    eligible = _verdict(EligibilityVerdict.ELIGIBLE, MatchStrength.STRONG)

    ranked = rank_matches([ineligible_1, eligible, ineligible_2])

    assert ranked[0] is eligible
    assert ranked[1] in (ineligible_1, ineligible_2)
    assert ranked[2] in (ineligible_1, ineligible_2)
    assert ranked[1] is not ranked[2]
