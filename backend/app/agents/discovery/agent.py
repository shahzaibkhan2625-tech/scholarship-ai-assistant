"""Discovery Agent (T089, Blueprint §7.3, §30.3; PRD B3 Hard-Gated Zone).

Builds a **multi-strategy query plan** across profile dimensions — country,
field, degree level, nationality (§30.3: "rather than one generic query") —
and, for each plan step, calls ONLY the already governance-gated tools
(`api_connector_tool`, `listing_fetch_tool`) built on top of
`source_repo.get_active_sources`/`get_source_by_id` (T071). This
module never writes to `source_registry`/`candidate_sources`/
`source_fetch_log` and never bypasses the active-only gate those repo
functions already enforce — verified by static AST inspection in
`tests/agents/test_discovery_guardrails.py` (no self-authorized sources, PRD
B3): the agent may call `source_repo.get_active_sources`/`get_source_by_id`
and NOTHING else — every write/log method lives one layer down, in the
connector tier (see `sources/connectors/official_fetch.py`'s module
docstring for why `fetch_and_extract_listing`'s logging lives there and not
here).

Every structured candidate a tool call surfaces is handed, unmodified, to the
existing `ingestion` workflow (T087) — extraction/normalization/dedup/
verify/classify/store all happen there, never re-implemented here. A
tool/source failure is never swallowed into "no scholarships found": the
connector layer underneath `official_fetch_tool`/`api_connector_tool`/
`listing_fetch_tool` already writes a `source_fetch_log` row on every
attempt (success or failure), and this agent's output ALWAYS includes a T084
coverage summary derived from those same rows — a failure surfaces as a
measured coverage gap, never silence (Blueprint §33; PRD B3 "failed fetch ≠
none found").

**Listing-page extraction (T135, Resolution note 5 — RESOLVED):**
`official_fetch`/web sources' raw HTML is now parsed into structured
candidates via `listing_fetch_tool` (`app.tools.listing_fetch`), which wraps
`extract_listing_from_page` (the multi-result sibling of `extract_requirements`,
T045's single already-identified page) behind the same connector-owned
fetch/log/gate pattern `official_fetch_tool` already uses. Every listing-
derived candidate reaches `run_ingestion` with `official_source_confirmed=
False` — a listing/index page is one step further removed than a page
already confirmed to be a specific scholarship's own official page, so its
candidates are never treated as officially confirmed.

Because the query plan fans out per profile *dimension* (not per source —
every non-`country` dimension value pulls in every active discovery-role
source regardless of country), the same web source can appear many times in
one `run_discovery()` call. `listing_fetch_tool`'s connector applies a
two-layer cache so this stays affordable against this project's documented
free-tier Gemini quota (T135/A9): (1) a real extraction only runs once per
source per `update_frequency`-derived staleness window across separate
`run_discovery()` calls (`status="cached"` reuses persisted
`scholarship_sources` rows instead), and (2) this agent additionally never
calls the tool twice for the same source within one run (`web_result_cache`
below — pure in-memory memoization of a tool call's own result, not a
`source_repo` access, so it doesn't touch the guardrail above).

**Scope note (per T135's explicit instructions — do not read this as "US4
complete"):** this resolves spec.md US4 Acceptance Scenario 1 (ranked results
across ≥2 governed source types) only.

**Scenario 3 (US4 Acceptance Scenario 3 — RESOLVED, ADR-0005):** a website
not yet in the registry, encountered during a listing-page fetch, is now
recorded as a `pending` `candidate_sources` row. That detection/write path
lives entirely in the connector tier (`sources/connectors/official_fetch.py`'s
`_detect_and_record_candidate_sources`, calling `app.tools.detect_candidate_links`
+ `source_repo.record_or_bump_candidate_source`), for the exact same reason
the rest of this module's write/log methods live there — this agent module
still never touches `source_repo` beyond `get_active_sources`/`get_source_by_id`,
verified by the same guardrail suite plus its connector-tier sibling
(`tests/sources/test_official_fetch_guardrails.py`).
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
from app.tools.listing_fetch import ListingFetchOutput, listing_fetch_tool
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


def _ingest_candidates(
    db: Session,
    step: QueryPlanStep,
    source: Any,
    candidates: list[dict],
    *,
    fetch_errors: list[str],
    step_label: str,
    official_source_confirmed: bool = False,
) -> list[DiscoveryResultItem]:
    step_results: list[DiscoveryResultItem] = []
    for candidate in candidates:
        if not candidate.get("name"):
            continue  # ingestion's STORE gate requires `name`; nothing to hand off without it
        ingestion_state = run_ingestion(
            db,
            source_id=step.source_id,
            source_url=f"https://{source.domain}",
            raw=candidate,
            official_source_confirmed=official_source_confirmed,
        )
        scholarship = ingestion_state.get("scholarship")
        if scholarship is None:
            fetch_errors.append(
                f"{step_label}: ingestion failed at {ingestion_state.get('error_stage')}: "
                f"{ingestion_state.get('error')}"
            )
            continue
        step_results.append(
            DiscoveryResultItem(
                scholarship_id=scholarship.id,
                source_id=step.source_id,
                source_type=step.source_type,
                verification_status=scholarship.verification_status,
            )
        )
    return step_results


def _run_web_listing_step(
    db: Session,
    step: QueryPlanStep,
    source: Any,
    *,
    listing_fetch: Callable[..., ListingFetchOutput],
    fetch_errors: list[str],
    web_result_cache: dict[uuid.UUID, list[DiscoveryResultItem]],
    step_label: str,
) -> list[DiscoveryResultItem]:
    """T135: `listing_fetch_tool` owns the fetch, the LLM extraction, the
    grounding check, and every `source_repo` read/write for the extraction
    step (see module docstring — the agent never touches `source_repo`
    beyond the plan-building reads). This function's only job is: don't call
    the tool twice for the same source in one run (`web_result_cache`), and
    shape whichever of the tool's three outcomes ("cached" / "ok" / "fail")
    into this run's `results`/`fetch_errors`."""

    if step.source_id in web_result_cache:
        return web_result_cache[step.source_id]

    outcome = listing_fetch(db, step.source_id, f"https://{source.domain}")

    if outcome.status == "cached":
        step_results = [
            DiscoveryResultItem(
                scholarship_id=ref.scholarship_id,
                source_id=step.source_id,
                source_type=step.source_type,
                verification_status=ref.verification_status,
            )
            for ref in outcome.cached
        ]
        web_result_cache[step.source_id] = step_results
        return step_results

    if outcome.status != "ok":
        # A failed fetch/extraction is never treated as "no scholarships
        # found" here — the connector already wrote a source_fetch_log row
        # (success or failure), which the coverage summary (below) reflects
        # as a measured gap.
        fetch_errors.append(f"{step_label}: {outcome.error}")
        web_result_cache[step.source_id] = []
        return []

    step_results = _ingest_candidates(
        db, step, source, outcome.candidates, fetch_errors=fetch_errors, step_label=step_label,
        official_source_confirmed=False,  # T135/A3: a listing page is never treated as an official confirmation
    )
    web_result_cache[step.source_id] = step_results
    return step_results


def _run_step(
    db: Session,
    step: QueryPlanStep,
    *,
    fetch_api: Callable[..., ApiConnectorOutput],
    listing_fetch: Callable[..., ListingFetchOutput],
    results: list[DiscoveryResultItem],
    fetch_errors: list[str],
    web_result_cache: dict[uuid.UUID, list[DiscoveryResultItem]],
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
            if outcome.status != "ok":
                fetch_errors.append(f"{step_label}: {outcome.error}")
                return
            step_results = _ingest_candidates(
                db, step, source, outcome.records, fetch_errors=fetch_errors, step_label=step_label
            )
        else:
            step_results = _run_web_listing_step(
                db, step, source,
                listing_fetch=listing_fetch,
                fetch_errors=fetch_errors,
                web_result_cache=web_result_cache,
                step_label=step_label,
            )
    except Exception as exc:  # noqa: BLE001 - one tool's failure must never crash the whole discovery run
        fetch_errors.append(f"{step_label}: {exc}")
        return

    results.extend(step_results)


def run_discovery(
    db: Session,
    profile: Any,
    *,
    fetch_api: Callable[..., ApiConnectorOutput] = api_connector_tool,
    listing_fetch: Callable[..., ListingFetchOutput] = listing_fetch_tool,
) -> DiscoveryRunResult:
    """Plan -> retrieve (via gated tools only) -> hand candidates to T087
    ingestion -> always attach a T084 coverage summary. Never raises on a
    per-step failure; every failure is captured in `fetch_errors` AND already
    logged to `source_fetch_log` by the tool layer underneath.

    As of T135, `listing_fetch` (not a raw `fetch_official`) is the web-step
    tool boundary — it wraps its own `fetch_official` connector call
    internally (see `tools/listing_fetch.py`), so there is no separate
    `fetch_official` parameter here anymore."""

    plan = build_query_plan(db, profile)
    results: list[DiscoveryResultItem] = []
    fetch_errors: list[str] = []
    # Run-scoped only (T135/A9 layer 1) -- a fresh dict per run_discovery()
    # call, never persisted or shared across calls; see _run_web_listing_step.
    web_result_cache: dict[uuid.UUID, list[DiscoveryResultItem]] = {}

    for step in plan:
        _run_step(
            db, step,
            fetch_api=fetch_api,
            listing_fetch=listing_fetch,
            results=results,
            fetch_errors=fetch_errors,
            web_result_cache=web_result_cache,
        )

    coverage = get_coverage_summary(db)
    discovery_result = DiscoveryResult(results=results, coverage=coverage)
    return DiscoveryRunResult(query_plan=plan, discovery_result=discovery_result, fetch_errors=fetch_errors)
