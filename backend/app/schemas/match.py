"""MatchVerdict / CriterionOutcome — fixed, schema-locked, no free-text
verdict field (constitution Principle II). Frozen so nothing downstream
(including the Matching agent) can mutate a verdict after it is produced;
a corrected verdict is always a new object, never an in-place edit."""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class EligibilityVerdict(StrEnum):
    ELIGIBLE = "eligible"
    LIKELY = "likely"
    POSSIBLY = "possibly"
    NOT = "not"
    UNKNOWN_REQUIRES_VERIFICATION = "unknown_requires_verification"


class MatchStrength(StrEnum):
    STRONG = "strong"
    POSSIBLE = "possible"
    NOT = "not"


class CriterionResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    INFERRED = "inferred"
    MET = "met"
    UNMET = "unmet"


class CriterionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion: str
    result: CriterionResult
    evidence: str | None = None


class MatchVerdict(BaseModel):
    """Fixed, schema-locked — never a free-text verdict (constitution
    Principle II). `eligibility_verdict` MUST be NOT whenever any
    `hard_constraints[].result == fail`, enforced by the model_validator
    below (defense in depth alongside the hard_constraints service and the
    Matching-agent output guardrail — see agents/matching/agent.py)."""

    model_config = ConfigDict(frozen=True)

    scholarship_id: uuid.UUID
    eligibility_verdict: EligibilityVerdict
    match_strength: MatchStrength
    hard_constraints: list[CriterionOutcome] = []
    soft_preferences: list[CriterionOutcome] = []
    exclusions_triggered: list[str] = []
    matched_criteria: list[str] = []
    failed_criteria: list[str] = []
    missing_information: list[str] = []
    unverified_criteria: list[str] = []
    required_documents: list[str] = []
    remaining_actions: list[str] = []
    evidence: list[str] = []

    @model_validator(mode="after")
    def _hard_constraint_failure_forces_not(self) -> "MatchVerdict":
        any_hard_failure = any(c.result == CriterionResult.FAIL for c in self.hard_constraints)
        if any_hard_failure and self.eligibility_verdict != EligibilityVerdict.NOT:
            raise ValueError(
                "A hard-constraint failure MUST force eligibility_verdict='not', "
                f"got {self.eligibility_verdict!r} (constitution Principle II)."
            )
        return self

    @model_validator(mode="after")
    def _evidence_required_for_matched_and_failed(self) -> "MatchVerdict":
        for outcome in [*self.hard_constraints, *self.soft_preferences]:
            if outcome.result in (CriterionResult.PASS, CriterionResult.FAIL, CriterionResult.MET, CriterionResult.UNMET):
                if not outcome.evidence:
                    raise ValueError(
                        f"Criterion {outcome.criterion!r} with result {outcome.result!r} "
                        "requires an evidence reference (evidence-required guardrail)."
                    )
        return self


class UrlMatchResult(BaseModel):
    scholarship_id: uuid.UUID
    official_source: str
    verdict: MatchVerdict


class FetchFailure(BaseModel):
    url: str
    error: str
    reported_as: str = "fetch_failure"  # never "not eligible" — US2 Scenario 4
