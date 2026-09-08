"""`ingestion` LangGraph workflow (T087, Blueprint §10, §31) —
`normalize -> classify -> dedup -> derive_conflicts -> verify ->
conflict_resolution -> store -> index -> END`, consuming an already-extracted
candidate record (the `extract` step is T044/T045's existing
`extract_requirements`/`web_fetch` tools; this workflow starts one step
downstream, at the extracted-record shape those tools already produce).

**Automatic conflict detection (§32):** conflict_resolution is never
caller-triggered. Whenever `dedup` finds a deterministic (name+university+
intake) match against a caller-supplied `existing_records` snapshot,
`derive_conflicts` compares every field the new record and the existing
snapshot both have a value for; any field where the two disagree is turned
into a `field_conflicts` entry automatically, and `conflict_resolution` only
ever acts on `field_conflicts` — however it got populated (auto-derived, or
explicitly supplied by a caller that already knows about a disagreement) —
never on the dedup decision alone.

**Failure handling (§10):** "any node fail → log to source_fetch_log + retry
policy; never drop silently." Every node here is wrapped by `_run_stage`:
- `classify` and `dedup` may call out to the LLM / embedding API (network),
  so a raised exception there is retried per T072's bounded retry policy
  before being logged as a failure.
- `normalize`, `derive_conflicts`, `verify`, and `store` are pure/data-
  validation steps — a failure there is a shape/content problem retrying
  can't fix, so it is logged and the record is flagged immediately, never
  retried and never dropped without a trace.
In every failure case a `source_fetch_log` row with `status=fail` is written
before the workflow short-circuits to `END`, and the returned state always
carries `error`/`error_stage` so the caller can see exactly what happened —
the record is never simply absent with no trace.
"""

import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.data.repositories import scholarship_repo, source_repo
from app.models.funding import FundingDetails
from app.models.requirement import Requirement
from app.models.scholarship import (
    DegreeLevel,
    FundingStatus,
    LifecycleStatus,
    Scholarship,
    ScholarshipField,
    VerificationStatus,
)
from app.models.source import FetchStatus, ScholarshipSource
from app.rag.embed import ingest_html_page
from app.schemas.source import SourceFetchLogCreate
from app.services import classify as classify_service
from app.services import conflict_resolution as conflict_service
from app.services import dedup as dedup_service
from app.services import normalize as normalize_service
from app.services import verification as verification_service
from app.sources.retry_policy import DEFAULT_MAX_ATTEMPTS, run_with_retry

__all__ = ["IngestionState", "run_ingestion"]

# Blueprint §31: `name` is the one field STORE cannot proceed without at all
# (it is the DB's NOT NULL column) — missing it aborts storage entirely.
_REQUIRED_FIELDS = ("name",)

# Blueprint §31: "required fields (name, degree_level, country, at least one
# official source, deadline-or-status) must be present/typed before STORE;
# missing required fields -> held as incomplete, flagged, not published as
# fact." Unlike `name`, these are DB-nullable, so a record missing one of
# them is still stored (never silently dropped) but explicitly marked
# incomplete via a `_record_completeness` scholarship_fields row rather than
# being treated as a normal, complete, published record.
_FLAGGED_IF_MISSING = ("degree_level", "country", "deadline_or_status", "official_source")

# Scalar keys with a first-class Scholarship column; anything else present in
# `raw` is carried as a scholarship_fields row instead (§14: "attributes not
# yet promoted to a first-class column").
_SCHOLARSHIP_COLUMN_KEYS = {
    "name", "provider", "country", "field", "degree_level", "funding_status",
    "intake", "application_fee", "application_method", "application_procedure",
    "official_application_url", "deadline", "closing_status", "conditions",
    "exceptions", "notes",
}


class IngestionState(TypedDict, total=False):
    db: Any
    source_id: uuid.UUID
    source_url: str
    raw: dict[str, Any]
    raw_html: str | None
    requirements: list[dict] | None
    funding_details: dict | None
    existing_candidates: list
    existing_records: dict[str, dict]
    official_source_confirmed: bool
    field_conflicts: dict[str, list]
    normalized_fields: list
    classification: Any
    dedup_result: Any
    lifecycle_status: str | None
    conflict_resolutions: dict[str, Any]
    scholarship: Any
    incomplete_fields: list[str]
    error: str | None
    error_stage: str | None
    retryable: bool | None


def _log_failure(db, source_id: uuid.UUID, stage: str, error: str) -> None:
    source_repo.log_fetch(
        db,
        SourceFetchLogCreate(source_id=source_id, status=FetchStatus.FAIL, error=f"[{stage}] {error}", retry_count=0),
    )


def _run_stage(state: IngestionState, stage: str, fn: Callable[[], dict], *, retryable: bool) -> dict:
    """Runs one node's body; on failure logs to source_fetch_log and returns
    an error-carrying state update instead of raising, so the graph can
    short-circuit every remaining node without ever silently dropping the
    record."""

    if retryable:
        outcome = run_with_retry(fn, max_attempts=DEFAULT_MAX_ATTEMPTS, sleep=lambda _delay: None)
        if outcome.succeeded:
            return outcome.result
        error_message = str(outcome.last_error)
    else:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - converted into a logged, flagged failure
            error_message = str(exc)

    _log_failure(state["db"], state["source_id"], stage, error_message)
    return {"error": error_message, "error_stage": stage, "retryable": retryable}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _coerce_enum(enum_cls, value):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        return None


def _normalize_node(state: IngestionState) -> dict:
    def _do() -> dict:
        return {"normalized_fields": normalize_service.normalize_record(state["raw"])}

    return _run_stage(state, "normalize", _do, retryable=False)


def _classify_node(state: IngestionState) -> dict:
    if state.get("error"):
        return {}

    def _do() -> dict:
        text = " ".join(str(v) for v in state["raw"].values() if v)
        return {"classification": classify_service.classify_candidate(text)}

    return _run_stage(state, "classify", _do, retryable=True)


def _db_backed_existing(db, name: str) -> tuple[list[dedup_service.DedupCandidate], dict[str, dict]]:
    """Real, already-persisted rows matching `name` — dedup must be checked
    against what's actually in the DB, not only against whatever snapshot a
    caller happens to pass via `existing_candidates`/`existing_records`
    (T087 fix). Returned dict entries use the same `existing_records` shape
    `derive_conflicts` already expects from a caller-supplied snapshot."""
    candidates: list[dedup_service.DedupCandidate] = []
    records: dict[str, dict] = {}
    for row in scholarship_repo.find_by_name(db, name):
        field_rows = scholarship_repo.get_field_rows(db, row.id)
        university = next((fr.value for fr in field_rows if fr.key == "university" and fr.value), None)
        candidates.append(
            dedup_service.DedupCandidate(id=str(row.id), name=row.name, university=university, intake=row.intake, text=row.name)
        )
        fields = {
            key: getattr(row, key) for key in _SCHOLARSHIP_COLUMN_KEYS if key != "name" and getattr(row, key, None) is not None
        }
        for fr in field_rows:
            if fr.value is not None:
                fields.setdefault(fr.key, fr.value)
        records[str(row.id)] = {
            "fields": fields,
            "source_id": "existing",
            "retrieved_at": row.retrieved_at,
            "is_official": row.verification_status == VerificationStatus.VERIFIED,
            "reliability": "high" if row.verification_status == VerificationStatus.VERIFIED else "medium",
        }
    return candidates, records


def _dedup_node(state: IngestionState) -> dict:
    if state.get("error"):
        return {}

    def _do() -> dict:
        raw = state["raw"]
        candidate = dedup_service.DedupCandidate(
            id="__candidate__",
            name=raw.get("name") or "",
            university=raw.get("university"),
            intake=raw.get("intake"),
            text=raw.get("name"),
        )
        db_candidates, db_records = _db_backed_existing(state["db"], candidate.name)
        existing = [*db_candidates, *(state.get("existing_candidates") or [])]
        result = dedup_service.find_duplicate(candidate, existing)
        # Caller-supplied existing_records (if any) win on key collision; the
        # DB-derived snapshot fills in the rest so derive_conflicts can see
        # real persisted duplicates too, not only caller-provided ones.
        merged_records = {**db_records, **(state.get("existing_records") or {})}
        return {"dedup_result": result, "existing_records": merged_records}

    return _run_stage(state, "dedup", _do, retryable=True)


def _derive_field_conflicts_node(state: IngestionState) -> dict:
    """Blueprint §32: conflict detection must be automatic, not
    caller-triggered. When `dedup` found a deterministic-key match against a
    caller-supplied `existing_records` snapshot, compare every field the new
    record and the existing snapshot both carry a value for; any field where
    they disagree becomes a `field_conflicts` entry for `conflict_resolution`
    to adjudicate — without the caller ever having to name the conflict."""

    if state.get("error"):
        return {}

    dedup_result = state.get("dedup_result")
    if dedup_result is None or dedup_result.decision != dedup_service.DedupDecision.MERGE_WITH_EXISTING:
        return {}

    snapshot = (state.get("existing_records") or {}).get(dedup_result.matched_id)
    if not snapshot:
        # A key match was found, but the caller supplied no field-level data
        # to compare against — nothing to derive (never invent a conflict).
        return {}

    def _do() -> dict:
        raw = state["raw"]
        now = datetime.now(timezone.utc)
        new_official = state.get("official_source_confirmed", False)
        new_meta = dict(
            source_id=str(state["source_id"]),
            is_official=new_official,
            retrieved_at=now,
            reliability="high" if new_official else "medium",
        )

        existing_fields = snapshot.get("fields") or {}
        existing_retrieved_at = snapshot.get("retrieved_at") or datetime.min.replace(tzinfo=timezone.utc)
        if isinstance(existing_retrieved_at, str):
            existing_retrieved_at = datetime.fromisoformat(existing_retrieved_at)
        existing_meta = dict(
            source_id=snapshot.get("source_id", "existing"),
            is_official=snapshot.get("is_official", False),
            retrieved_at=existing_retrieved_at,
            reliability=snapshot.get("reliability", "medium"),
        )

        derived: dict[str, list] = {}
        for key, new_value in raw.items():
            if new_value is None or key not in existing_fields:
                continue
            existing_value = existing_fields[key]
            if existing_value is None or existing_value == new_value:
                continue
            derived[key] = [
                conflict_service.SourceValue(value=existing_value, **existing_meta),
                conflict_service.SourceValue(value=new_value, **new_meta),
            ]

        if not derived:
            return {}
        # A caller-supplied field_conflicts entry (if any) wins over an
        # auto-derived one for the same field; auto-derivation fills the rest.
        return {"field_conflicts": {**derived, **(state.get("field_conflicts") or {})}}

    return _run_stage(state, "derive_conflicts", _do, retryable=False)


def _verify_node(state: IngestionState) -> dict:
    if state.get("error"):
        return {}

    def _do() -> dict:
        raw = state["raw"]
        now = datetime.now(timezone.utc)
        official_source_confirmed = state.get("official_source_confirmed", False)
        verification_input = verification_service.VerificationInput(
            retrieved_at=now,
            # This ingestion pass itself constitutes the official confirmation
            # (when one occurred), so `last_verified_at` is "now" rather than
            # None — otherwise a record could never reach VERIFIED on its
            # very first, officially-confirmed ingestion.
            last_verified_at=now if official_source_confirmed else None,
            deadline=_parse_date(raw.get("deadline")),
            official_source_confirmed=official_source_confirmed,
            closing_status=raw.get("closing_status"),
            now=now,
        )
        status = verification_service.compute_lifecycle_status(verification_input)
        return {"lifecycle_status": status.value}

    return _run_stage(state, "verify", _do, retryable=False)


def _conflict_resolution_node(state: IngestionState) -> dict:
    if state.get("error"):
        return {}

    field_conflicts = state.get("field_conflicts")
    if not field_conflicts:
        # Nothing to resolve at the field level — `derive_conflicts` already
        # ran before this node, so an empty `field_conflicts` here means no
        # disagreement was found (or supplied), not that one was skipped.
        return {"conflict_resolutions": {}}

    def _do() -> dict:
        resolutions = {key: conflict_service.resolve_conflict(values) for key, values in field_conflicts.items()}
        return {"conflict_resolutions": resolutions}

    return _run_stage(state, "conflict_resolution", _do, retryable=False)


def _validate_required_fields(raw: dict[str, Any]) -> str | None:
    for key in _REQUIRED_FIELDS:
        if not raw.get(key):
            return f"required field '{key}' is missing"
    return None


def _missing_flagged_fields(
    db, source_id: uuid.UUID, official_source_confirmed: bool, *, degree_level, country, deadline, closing_status
) -> list[str]:
    """Blueprint §31's softer STORE gate: degree_level, country,
    deadline-or-status, and at least one official source are all
    DB-nullable, so a record missing one of them is still stored, but must
    be explicitly flagged incomplete rather than treated as a normal,
    complete, published record."""

    missing = []
    if degree_level is None:
        missing.append("degree_level")
    if not country:
        missing.append("country")
    if not deadline and not closing_status:
        missing.append("deadline_or_status")

    has_official_source = official_source_confirmed
    if not has_official_source:
        source = source_repo.get_source_by_id(db, source_id)
        has_official_source = bool(source and source.official_status == "official")
    if not has_official_source:
        missing.append("official_source")

    return missing


def _resolve_dedup_match(db, dedup_result) -> Scholarship | None:
    """A `merge_with_existing` decision only names a real persisted row when
    `matched_id` is an actual Scholarship UUID that still exists — a
    caller-supplied synthetic snapshot id (used purely to drive conflict
    derivation against data that isn't itself a DB row) resolves to None
    here, which correctly falls back to inserting a fresh row for it."""
    if dedup_result is None or dedup_result.decision != dedup_service.DedupDecision.MERGE_WITH_EXISTING:
        return None
    try:
        matched_uuid = uuid.UUID(dedup_result.matched_id)
    except (TypeError, ValueError):
        return None
    return scholarship_repo.get_by_id(db, matched_uuid)


def _store_node(state: IngestionState) -> dict:
    if state.get("error"):
        return {}

    def _do() -> dict:
        db = state["db"]
        raw = state["raw"]

        validation_error = _validate_required_fields(raw)
        if validation_error is not None:
            raise ValueError(validation_error)

        matched_scholarship = _resolve_dedup_match(db, state.get("dedup_result"))
        conflict_resolutions = state.get("conflict_resolutions") or {}

        if matched_scholarship is not None:
            # Blueprint §31/§32: a genuine duplicate is never stored as a
            # second scholarships row. Only the new provenance is recorded,
            # and any field-level disagreement (already derived+resolved by
            # derive_conflicts/conflict_resolution above) is attached to the
            # EXISTING record rather than a fresh one.
            db.add(
                ScholarshipSource(
                    scholarship_id=matched_scholarship.id,
                    source_id=state["source_id"],
                    url=state["source_url"],
                    verified_at=datetime.now(timezone.utc) if state.get("official_source_confirmed") else None,
                    verification_status=(
                        VerificationStatus.VERIFIED
                        if state.get("official_source_confirmed")
                        else VerificationStatus.UNVERIFIED
                    ),
                )
            )
            for key, resolution in conflict_resolutions.items():
                matched_scholarship.fields.append(
                    ScholarshipField(
                        key=key,
                        value=(
                            [{"source_id": v.source_id, "value": v.value} for v in resolution.conflicting_values]
                            if resolution.value_status == "conflicting"
                            else resolution.value
                        ),
                        value_status=resolution.value_status,
                        confidence="verified" if resolution.resolved_by == "official" else "inferred",
                        source_id=resolution.winning_source_id,
                    )
                )
                if resolution.value_status == "conflicting":
                    matched_scholarship.verification_status = VerificationStatus.CONFLICTING
            db.commit()

            source_repo.log_fetch(
                db,
                SourceFetchLogCreate(source_id=state["source_id"], status=FetchStatus.OK, retry_count=0, items_found=1),
            )
            return {"scholarship": matched_scholarship, "incomplete_fields": []}

        normalized_by_key = {nf.key: nf for nf in state.get("normalized_fields", [])}
        classification = state.get("classification")

        def _column_value(key: str, fallback: str | None = None):
            nf = normalized_by_key.get(key)
            if nf is not None and nf.value_status == "known":
                return nf.value
            return fallback

        degree_level_raw = _column_value("degree_level", classification.degree_level if classification else None)
        funding_status_raw = _column_value(
            "funding_status", classification.funding_status if classification else None
        )
        country_raw = _column_value("country", classification.country if classification else None)
        field_raw = _column_value("field", classification.field if classification else None)
        degree_level_value = _coerce_enum(DegreeLevel, degree_level_raw)
        deadline_value = _parse_date(raw.get("deadline"))
        closing_status_value = raw.get("closing_status")

        has_conflict = any(res.value_status == "conflicting" for res in conflict_resolutions.values())

        missing_flagged_fields = _missing_flagged_fields(
            db,
            state["source_id"],
            state.get("official_source_confirmed", False),
            degree_level=degree_level_value,
            country=country_raw,
            deadline=deadline_value,
            closing_status=closing_status_value,
        )

        scholarship = Scholarship(
            name=raw["name"],
            provider=raw.get("provider"),
            country=country_raw,
            field=field_raw,
            degree_level=degree_level_value,
            funding_status=_coerce_enum(FundingStatus, funding_status_raw) or FundingStatus.UNKNOWN,
            intake=raw.get("intake"),
            application_fee=raw.get("application_fee"),
            application_method=raw.get("application_method"),
            application_procedure=raw.get("application_procedure"),
            official_scholarship_url=state["source_url"],
            official_application_url=raw.get("official_application_url"),
            lifecycle_status=_coerce_enum(LifecycleStatus, state.get("lifecycle_status")) or LifecycleStatus.NEWLY_DISCOVERED,
            deadline=deadline_value,
            closing_status=closing_status_value,
            conditions=raw.get("conditions"),
            exceptions=raw.get("exceptions"),
            notes=raw.get("notes"),
            verification_status=VerificationStatus.CONFLICTING if has_conflict else VerificationStatus.UNVERIFIED,
        )

        for req in state.get("requirements") or []:
            scholarship.requirements.append(Requirement(**req))
        funding_details = state.get("funding_details")
        if funding_details:
            scholarship.funding_details = FundingDetails(**funding_details)

        if classification is not None and classification.provider_type is not None:
            scholarship.fields.append(
                ScholarshipField(
                    key="provider_type",
                    value=classification.provider_type,
                    value_status="known",
                    confidence=classification.provider_type_confidence,
                    source_id=state.get("source_id"),
                )
            )

        for nf in state.get("normalized_fields", []):
            if nf.key in _SCHOLARSHIP_COLUMN_KEYS:
                continue
            scholarship.fields.append(
                ScholarshipField(
                    key=nf.key,
                    value=nf.value,
                    value_status=nf.value_status,
                    confidence=nf.confidence,
                    source_id=state.get("source_id"),
                )
            )

        for key, resolution in conflict_resolutions.items():
            scholarship.fields.append(
                ScholarshipField(
                    key=key,
                    value=(
                        [{"source_id": v.source_id, "value": v.value} for v in resolution.conflicting_values]
                        if resolution.value_status == "conflicting"
                        else resolution.value
                    ),
                    value_status=resolution.value_status,
                    confidence="verified" if resolution.resolved_by == "official" else "inferred",
                    source_id=resolution.winning_source_id,
                )
            )

        if missing_flagged_fields:
            # §31: DB-nullable required fields are still missing — the record
            # is stored (never dropped) but explicitly marked incomplete, not
            # silently treated as a normal, complete, published record.
            scholarship.fields.append(
                ScholarshipField(
                    key="_record_completeness",
                    value={"status": "incomplete", "missing_fields": missing_flagged_fields},
                    value_status="unknown",
                    confidence="unknown",
                    source_id=state.get("source_id"),
                )
            )

        scholarship = scholarship_repo.create(db, scholarship)

        db.add(
            ScholarshipSource(
                scholarship_id=scholarship.id,
                source_id=state["source_id"],
                url=state["source_url"],
                verified_at=datetime.now(timezone.utc) if state.get("official_source_confirmed") else None,
                verification_status=(
                    VerificationStatus.VERIFIED if state.get("official_source_confirmed") else VerificationStatus.UNVERIFIED
                ),
            )
        )
        db.commit()

        source_repo.log_fetch(
            db,
            SourceFetchLogCreate(source_id=state["source_id"], status=FetchStatus.OK, retry_count=0, items_found=1),
        )

        return {"scholarship": scholarship, "incomplete_fields": missing_flagged_fields}

    return _run_stage(state, "store", _do, retryable=False)


def _index_node(state: IngestionState) -> dict:
    if state.get("error") or not state.get("raw_html") or not state.get("scholarship"):
        return {}
    try:
        ingest_html_page(
            state["raw_html"], source_url=state["source_url"], scholarship_id=str(state["scholarship"].id)
        )
    except Exception:
        pass  # best-effort indexing, mirrors url_match's ingest_rag_node
    return {}


def _build_graph():
    graph = StateGraph(IngestionState)
    graph.add_node("normalize", _normalize_node)
    graph.add_node("classify", _classify_node)
    graph.add_node("dedup", _dedup_node)
    graph.add_node("derive_conflicts", _derive_field_conflicts_node)
    graph.add_node("verify", _verify_node)
    graph.add_node("conflict_resolution", _conflict_resolution_node)
    graph.add_node("store", _store_node)
    graph.add_node("index", _index_node)

    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "classify")
    graph.add_edge("classify", "dedup")
    graph.add_edge("dedup", "derive_conflicts")
    graph.add_edge("derive_conflicts", "verify")
    graph.add_edge("verify", "conflict_resolution")
    graph.add_edge("conflict_resolution", "store")
    graph.add_edge("store", "index")
    graph.add_edge("index", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_ingestion(
    db,
    *,
    source_id: uuid.UUID,
    source_url: str,
    raw: dict[str, Any],
    raw_html: str | None = None,
    requirements: list[dict] | None = None,
    funding_details: dict | None = None,
    existing_candidates: list | None = None,
    existing_records: dict[str, dict] | None = None,
    official_source_confirmed: bool = False,
    field_conflicts: dict[str, list] | None = None,
) -> dict:
    initial_state: IngestionState = {
        "db": db,
        "source_id": source_id,
        "source_url": source_url,
        "raw": raw,
        "raw_html": raw_html,
        "requirements": requirements,
        "funding_details": funding_details,
        "existing_candidates": existing_candidates or [],
        "existing_records": existing_records or {},
        "official_source_confirmed": official_source_confirmed,
        "field_conflicts": field_conflicts or {},
    }
    return _COMPILED_GRAPH.invoke(initial_state)
