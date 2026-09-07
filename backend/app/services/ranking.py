"""`ranking` service — deterministic ranking formula. The Matching agent
supplies per-criterion inputs only; it never reorders results itself
(constitution Principle II: "ranking formula lives in code"). A verdict
whose eligibility_verdict is NOT always ranks last, regardless of
match_strength — a hard-constraint failure can never be out-ranked by a
strong soft-preference match."""

from app.schemas.match import EligibilityVerdict, MatchStrength, MatchVerdict

_VERDICT_RANK = {
    EligibilityVerdict.ELIGIBLE: 0,
    EligibilityVerdict.LIKELY: 1,
    EligibilityVerdict.POSSIBLY: 2,
    EligibilityVerdict.UNKNOWN_REQUIRES_VERIFICATION: 3,
    EligibilityVerdict.NOT: 4,
}

_STRENGTH_RANK = {
    MatchStrength.STRONG: 0,
    MatchStrength.POSSIBLE: 1,
    MatchStrength.NOT: 2,
}


def sort_key(verdict: MatchVerdict) -> tuple[int, int]:
    return (_VERDICT_RANK[verdict.eligibility_verdict], _STRENGTH_RANK[verdict.match_strength])


def rank_matches(verdicts: list[MatchVerdict]) -> list[MatchVerdict]:
    return sorted(verdicts, key=sort_key)
