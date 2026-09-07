"""`url_match` LangGraph workflow: fetch -> extract -> verify/persist ->
hard_constraints -> Matching agent -> persist match.

A fetch failure short-circuits every downstream node and is reported
explicitly (FetchFailure) — never as "not eligible" (US2 Acceptance
Scenario 4)."""

import uuid
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.matching.agent import build_verdict, produce_soft_preference_outcomes
from app.core.llm import LlmNotConfiguredError
from app.data.repositories import match_repo, scholarship_repo
from app.data.vectors.qdrant_client import VectorStoreNotConfiguredError
from app.models.funding import FundingDetails
from app.models.match import Match
from app.models.requirement import Requirement, RequirementCategory
from app.models.scholarship import DegreeLevel, FundingStatus, Scholarship, VerificationStatus
from app.rag.embed import ingest_html_page
from app.rag.loaders import load_html
from app.schemas.match import FetchFailure, UrlMatchResult
from app.services.hard_constraints import evaluate_hard_constraints
from app.tools.extract_requirements import ExtractedScholarshipData, extract_requirements_from_page
from app.tools.web_fetch import fetch_url


class UrlMatchState(TypedDict, total=False):
    url: str
    user_id: uuid.UUID
    profile: Any
    db: Any
    fetch_html: str | None
    extracted: ExtractedScholarshipData | None
    scholarship: Scholarship | None
    hard_outcomes: list
    verdict: Any
    error: str | None


def _coerce_enum(enum_cls, value, default=None):
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _fetch_node(state: UrlMatchState) -> dict:
    result = fetch_url(state["url"])
    if not result.success:
        return {"error": result.error}
    return {"fetch_html": result.html}


def _extract_node(state: UrlMatchState) -> dict:
    if state.get("error"):
        return {}
    page_text = load_html(state["fetch_html"], source_url=state["url"], scholarship_id="").text
    extracted = extract_requirements_from_page(page_text, source_url=state["url"])
    return {"extracted": extracted}


def _persist_scholarship_node(state: UrlMatchState) -> dict:
    if state.get("error"):
        return {}
    db = state["db"]
    existing = scholarship_repo.get_by_official_url(db, state["url"])
    if existing is not None:
        return {"scholarship": existing}

    extracted = state["extracted"]
    scholarship = Scholarship(
        name=extracted.name,
        provider=extracted.provider,
        country=extracted.country,
        field=extracted.field,
        degree_level=_coerce_enum(DegreeLevel, extracted.degree_level),
        funding_status=_coerce_enum(FundingStatus, extracted.funding_status, default=FundingStatus.UNKNOWN),
        official_scholarship_url=state["url"],
        official_application_url=extracted.official_application_url,
        deadline=_parse_date(extracted.deadline),
        verification_status=VerificationStatus.UNVERIFIED,
    )
    for req in extracted.requirements:
        scholarship.requirements.append(
            Requirement(
                category=_coerce_enum(RequirementCategory, req.category, default=RequirementCategory.OTHER),
                key=req.key,
                value=req.value,
                mandatory=req.mandatory,
                value_status=req.value_status,
                confidence=req.confidence,
            )
        )
    if extracted.funding_details is not None:
        fd = extracted.funding_details
        scholarship.funding_details = FundingDetails(**fd.model_dump())

    scholarship = scholarship_repo.create(db, scholarship)
    return {"scholarship": scholarship}


def _ingest_rag_node(state: UrlMatchState) -> dict:
    if state.get("error"):
        return {}
    try:
        ingest_html_page(state["fetch_html"], source_url=state["url"], scholarship_id=str(state["scholarship"].id))
    except (VectorStoreNotConfiguredError, LlmNotConfiguredError):
        pass  # best-effort: Q&A grounding simply finds nothing yet if unconfigured
    return {}


def _hard_constraints_node(state: UrlMatchState) -> dict:
    if state.get("error"):
        return {}
    outcomes = evaluate_hard_constraints(state["profile"], state["scholarship"])
    return {"hard_outcomes": outcomes}


def _matching_node(state: UrlMatchState) -> dict:
    if state.get("error"):
        return {}
    hard_criteria_names = {o.criterion for o in state["hard_outcomes"]}
    soft_outcomes, exclusions = produce_soft_preference_outcomes(
        state["profile"], state["scholarship"], hard_outcome_criteria=hard_criteria_names
    )
    missing_information = [item.dimension for item in state["profile"].missing_info]
    verdict = build_verdict(state["scholarship"].id, state["hard_outcomes"], soft_outcomes, exclusions, missing_information)
    return {"verdict": verdict}


def _persist_match_node(state: UrlMatchState) -> dict:
    if state.get("error"):
        return {}
    verdict = state["verdict"]
    match_row = Match(
        user_id=state["user_id"],
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
    )
    match_repo.create(state["db"], match_row)
    return {}


def _build_graph():
    graph = StateGraph(UrlMatchState)
    graph.add_node("fetch", _fetch_node)
    graph.add_node("extract", _extract_node)
    graph.add_node("persist_scholarship", _persist_scholarship_node)
    graph.add_node("ingest_rag", _ingest_rag_node)
    graph.add_node("hard_constraints", _hard_constraints_node)
    graph.add_node("matching", _matching_node)
    graph.add_node("persist_match", _persist_match_node)

    graph.add_edge(START, "fetch")
    graph.add_edge("fetch", "extract")
    graph.add_edge("extract", "persist_scholarship")
    graph.add_edge("persist_scholarship", "ingest_rag")
    graph.add_edge("ingest_rag", "hard_constraints")
    graph.add_edge("hard_constraints", "matching")
    graph.add_edge("matching", "persist_match")
    graph.add_edge("persist_match", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_url_match(db, user_id: uuid.UUID, profile, url: str) -> UrlMatchResult | FetchFailure:
    initial_state: UrlMatchState = {"url": url, "user_id": user_id, "profile": profile, "db": db}
    result_state = _COMPILED_GRAPH.invoke(initial_state)

    if result_state.get("error"):
        return FetchFailure(url=url, error=result_state["error"])

    verdict = result_state["verdict"]
    scholarship = result_state["scholarship"]
    return UrlMatchResult(scholarship_id=scholarship.id, official_source=url, verdict=verdict)
