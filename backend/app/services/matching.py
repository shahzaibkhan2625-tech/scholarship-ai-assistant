"""Thin orchestration for matching an already-stored scholarship against the
current profile (no fetch/extract — that's the url_match workflow's job).
Shared by POST /scholarships/{id}/match and the url_match workflow's own
matching step conceptually, though url_match inlines its own graph node for
LangGraph state-machine reasons; this module exists for the "match a
scholarship already in our DB" entrypoint (US2's second endpoint)."""

import uuid

from sqlalchemy.orm import Session

from app.agents.matching.agent import build_verdict, produce_soft_preference_outcomes
from app.data.repositories import match_repo
from app.models.match import Match
from app.models.scholarship import Scholarship
from app.schemas.match import MatchVerdict
from app.services.hard_constraints import evaluate_hard_constraints


def match_scholarship(db: Session, user_id: uuid.UUID, profile, scholarship: Scholarship) -> MatchVerdict:
    hard_outcomes = evaluate_hard_constraints(profile, scholarship)
    hard_criteria_names = {o.criterion for o in hard_outcomes}
    soft_outcomes, exclusions = produce_soft_preference_outcomes(
        profile, scholarship, hard_outcome_criteria=hard_criteria_names
    )
    missing_information = [item.dimension for item in profile.missing_info]
    verdict = build_verdict(scholarship.id, hard_outcomes, soft_outcomes, exclusions, missing_information)

    match_repo.create(
        db,
        Match(
            user_id=user_id,
            scholarship_id=verdict.scholarship_id,
            eligibility_verdict=verdict.eligibility_verdict.value,
            match_strength=verdict.match_strength.value,
            hard_constraints=[o.model_dump(mode="json") for o in verdict.hard_constraints],
            soft_preferences=[o.model_dump(mode="json") for o in verdict.soft_preferences],
            exclusions_triggered=verdict.exclusions_triggered,
            matched_criteria=verdict.matched_criteria,
            failed_criteria=verdict.failed_criteria,
            missing_information=verdict.missing_information,
            unverified_criteria=verdict.unverified_criteria,
            required_documents=verdict.required_documents,
            remaining_actions=verdict.remaining_actions,
            evidence=verdict.evidence,
        ),
    )
    return verdict
