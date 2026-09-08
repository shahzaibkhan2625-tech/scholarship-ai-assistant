"""Discovery Agent (T089, Blueprint §7.3, §30.3; PRD B3 Hard-Gated Zone).

Builds a **multi-strategy query plan** across profile dimensions — country,
field, degree level, nationality (§30.3: "rather than one generic query") —
and, for each plan step, calls ONLY the already governance-gated tools
(`official_fetch_tool`, `api_connector_tool`, T074/T076) built on top of
`source_repo.get_active_sources`/`get_source_by_id` (T071). This module never
writes to `source_registry`/`candidate_sources` and never bypasses the
active-only gate those repo functions already enforce — verified by static
AST inspection in `tests/agents/test_discovery_guardrails.py` (no
self-authorized sources, PRD B3).

Every structured candidate a tool call surfaces is handed, unmodified, to the
existing `ingestion` workflow (T087) — extraction/normalization/dedup/
verify/classify/store all happen there, never re-implemented here. A
tool/source failure is never swallowed into "no scholarships found": the
connector layer underneath `official_fetch_tool`/`api_connector_tool`
already writes a `source_fetch_log` row on every attempt (success or
failure), and this agent's output ALWAYS includes a T084 coverage summary
derived from those same rows — a failure surfaces as a measured coverage
gap, never silence (Blueprint §33; PRD B3 "failed fetch ≠ none found").

**Interpretation note (flagged per task instructions, §30/§7.3 don't spell
this out literally):** `official_fetch`/web sources are still invoked here
(so registry gating, retries, and coverage/fetch-log accounting are
exercised for every source type, not just APIs) but their raw HTML is NOT
auto-parsed into multiple structured candidates in this slice — no
listing-page extraction tool exists yet (`extract_requirements`, T045, is
scoped to a single already-identified scholarship page for the url_match
flow, not a multi-result listing page). Only `api_connector`-sourced records
(already-structured dicts) are handed to `run_ingestion` here. Building a
listing-page extractor is left to a follow-up slice; until then, official/web
steps contribute to the query plan and to coverage/fetch-log accounting but
not to `results`.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.data.repositories import source_repo
from app.models.source import AccessMethod
from app.schemas.discovery import DiscoveryResult, DiscoveryResultItem
from app.services.coverage import get_coverage_summary
from app.tools.api_connector import ApiConnectorOutput, api_connector_tool
from app.tools.official_fetch import OfficialFetchOutput, official_fetch_tool
from app.workflows.ingestion.graph import run_ingestion

__all__ = ["QueryPlanStep", "DiscoveryRunResult", "build_query_plan", "run_discovery"]


@dataclass(frozen=True)
class QueryPlanStep:
    """One dimension-scoped strategy in the multi-strategy query plan
    (Blueprint §30.3) — the agent never issues a single generic query."""

    dimension: str  # "country" | "field" | "degree_level" | "nationality"
    value: str
    source_id: uuid.UUID
    source_type: str
    access_method: str  # AccessMethod value


@dataclass(frozen=True)
class DiscoveryRunResult:
    """The agent's full output. `discovery_result.coverage` is ALWAYS
    populated (T084), even when `discovery_result.results` is empty and even
    when every plan step failed — coverage is derived independently from
    `source_fetch_log`, not from whether this run happened to find anything."""

    query_plan: list[QueryPlanStep]
    discovery_result: DiscoveryResult
    fetch_errors: list[str]


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else value


def build_query_plan(db: Session, profile: Any) -> list[QueryPlanStep]:
    """One plan step per (dimension value, active discovery-role source)
    pair. A dimension with no profile value, or no matching active source,
    simply contributes no step for it — never a fabricated one. Only
    `source_repo.get_active_sources` (the active-only-gated read path) is
    used to resolve sources; nothing here can see a `pending`/`disabled`
    source or a `candidate_sources` row."""

    country_values = list(getattr(profile, "target_countries", None) or [])
    field_values = list(getattr(profile, "target_fields", None) or [])
    degree_level = getattr(profile, "target_degree_level", None)
    nationality = getattr(profile, "nationality", None)

    dimension_values: list[tuple[str, list[str]]] = [("country", country_values), ("field", field_values)]
    if degree_level:
        dimension_values.append(("degree_level", [_enum_value(degree_level)]))
    if nationality:
        dimension_values.append(("nationality", [nationality]))

    plan: list[QueryPlanStep] = []
    for dimension, values in dimension_values:
        for value in values:
            country_filter = value if dimension == "country" else None
            candidate_sources = [
                source for source in source_repo.get_active_sources(db, country=country_filter)
                if source.discovery_role
            ]
            for source in candidate_sources:
                plan.append(
                    QueryPlanStep(
                        dimension=dimension,
                        value=value,
                        source_id=source.id,
                        source_type=_enum_value(source.source_type),
                        access_method=_enum_value(source.access_method),
                    )
                )
    return plan


def _run_step(
    db: Session,
    step: QueryPlanStep,
    *,
    fetch_official: Callable[..., OfficialFetchOutput],
    fetch_api: Callable[..., ApiConnectorOutput],
    results: list[DiscoveryResultItem],
    fetch_errors: list[str],
) -> None:
    source = source_repo.get_source_by_id(db, step.source_id)
    if source is None:
        # Went inactive between planning and execution — never fetched,
        # never fabricated as a result or a false coverage signal.
        return

    step_label = f"[{step.dimension}={step.value}] source {step.source_id}"

    try:
        if step.access_method == AccessMethod.API.value:
            outcome = fetch_api(db, step.source_id, step.value)
            success, error = outcome.status == "ok", outcome.error
            candidates: list[dict] = outcome.records if success else []
        else:
            outcome = fetch_official(db, step.source_id, f"https://{source.domain}")
            success, error = outcome.status == "ok", outcome.error
            candidates = []  # see module docstring: listing-page extraction is a follow-up slice
    except Exception as exc:  # noqa: BLE001 - one tool's failure must never crash the whole discovery run
        fetch_errors.append(f"{step_label}: {exc}")
        return

    if not success:
        # A failed fetch is never treated as "no scholarships found" here —
        # the connector already wrote a source_fetch_log row, which the
        # coverage summary (below) reflects as a measured gap.
        fetch_errors.append(f"{step_label}: {error}")
        return

    for candidate in candidates:
        if not candidate.get("name"):
            continue  # ingestion's STORE gate requires `name`; nothing to hand off without it
        ingestion_state = run_ingestion(
            db,
            source_id=step.source_id,
            source_url=f"https://{source.domain}",
            raw=candidate,
        )
        scholarship = ingestion_state.get("scholarship")
        if scholarship is None:
            fetch_errors.append(
                f"{step_label}: ingestion failed at {ingestion_state.get('error_stage')}: "
                f"{ingestion_state.get('error')}"
            )
            continue
        results.append(
            DiscoveryResultItem(
                scholarship_id=scholarship.id,
                source_id=step.source_id,
                source_type=step.source_type,
                verification_status=scholarship.verification_status,
            )
        )


def run_discovery(
    db: Session,
    profile: Any,
    *,
    fetch_official: Callable[..., OfficialFetchOutput] = official_fetch_tool,
    fetch_api: Callable[..., ApiConnectorOutput] = api_connector_tool,
) -> DiscoveryRunResult:
    """Plan -> retrieve (via gated tools only) -> hand candidates to T087
    ingestion -> always attach a T084 coverage summary. Never raises on a
    per-step failure; every failure is captured in `fetch_errors` AND already
    logged to `source_fetch_log` by the tool layer underneath."""

    plan = build_query_plan(db, profile)
    results: list[DiscoveryResultItem] = []
    fetch_errors: list[str] = []

    for step in plan:
        _run_step(db, step, fetch_official=fetch_official, fetch_api=fetch_api, results=results, fetch_errors=fetch_errors)

    coverage = get_coverage_summary(db)
    discovery_result = DiscoveryResult(results=results, coverage=coverage)
    return DiscoveryRunResult(query_plan=plan, discovery_result=discovery_result, fetch_errors=fetch_errors)
