# ADR-0006: Monitoring Updates Existing Scholarships Through an Optional `run_ingestion` Update Path

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-07
- **Feature:** 002-source-monitoring-hardening (US1 — source monitoring, FR-MON-3/FR-MON-4)
- **Context:** US1 re-fetches every active web discovery source on a schedule and compares what the listing now says against what is already stored. When a stored scholarship's `deadline`, `funding_status` or `degree_level` differs, the stored record must be updated. Until now `run_ingestion` (`backend/app/workflows/ingestion/graph.py`) could only insert a new row, or — on a dedup match — append provenance/conflict rows and, per ADR-0002, **never rewrite scalar columns**. Monitoring needs exactly the thing ADR-0002 deliberately withholds: a scalar-column rewrite. FR-MON-4 additionally requires that monitoring introduce **no divergent data path** — everything that writes scholarship data must go through the one ingestion workflow.

## Decision

1. **`run_ingestion` gains one optional keyword, `existing_scholarship_id: uuid.UUID | None = None`.** Default `None` leaves every 001 and discovery call unchanged (the new state key is `None`, and every new branch is guarded on it being set). When set:
   - `dedup` short-circuits to that id (no DB name lookup, no embedding call);
   - `derive_conflicts` and `conflict_resolution` are skipped, even if a caller supplies `field_conflicts`;
   - `store` does a merge-style **update in place** instead of an insert: it writes only the monitored fields (`deadline`, `funding_status`, `degree_level` — `app/services/listing_diff.py`) and only when the listing states a different value. No new `scholarships` or `scholarship_sources` row is written, and `verification_status` is not touched.

2. **Every overwrite leaves an audit row first.** Before a value is replaced, its prior value is appended to the record as a `scholarship_fields` row: `key="_prior:<field>"`, `value={"value": <prior>, "replaced_at": <iso timestamp>}`, `value_status="known"`, `confidence="verified"` if the record was `VERIFIED` else `"inferred"`, `source_id` = the monitored source that observed the change. The underscore prefix follows the existing `_record_completeness` meta-key convention so the row is never read as the real field (`_db_backed_existing` uses `setdefault` by key).

3. **Policy: an unconfirmed (`official_source_confirmed=False`) listing value MAY overwrite a stored verified value.** This matches how discovery already treats listing data — a listing page is one step removed from a scholarship's own official page — and is the reason conflict resolution (official > recency > reliability) is skipped on this path: it would otherwise pin the older *official* value and defeat the point of monitoring a changing deadline. The prior value stays recoverable through the audit row. The listing's silence is never an overwrite: an unstated, unparseable, or `funding_status == "unknown"` value (the extractor's default) is "not stated", not "changed", so known data is never degraded to unknown.

4. **Interaction with ADR-0002.** ADR-0002 ("merges never rewrite scalar columns") remains fully in force for every caller that does not pass `existing_scholarship_id` — the default merge path is byte-for-byte as before. The scalar rewrite exists only on this opt-in path, only for the three monitored fields, and only with the audit row above. ADR-0002's known limitation (scalar columns can lag `scholarship_fields` on the *merge* path) is unchanged.

## Consequences

### Positive

- One write path (FR-MON-4): monitoring-driven writes go through the same normalize/classify/store workflow and the same failure logging (`_run_stage` -> `source_fetch_log`) as discovery; a failed update is logged with `error_stage`, never silently dropped.
- Backward compatible by construction: a defaulted keyword plus guards, with regression tests asserting the default.
- Fully auditable and reversible by hand: every overwritten value is retained.

### Negative

- `run_ingestion` now has two modes in one workflow, and the `store` node a third branch; readers must keep the guard (`existing_scholarship_id is not None`) in mind in four nodes.
- Overwriting verified data with unconfirmed listing data trades accuracy for freshness: a listing that is itself wrong now propagates into the scalar column. The audit row bounds the damage but does not prevent it.
- Only three fields are monitored. **"Requirement" changes cannot be detected at all** because the listing extractor never emits requirements data — FR-MON-3 is only partially covered, and this ADR does not claim otherwise.
- `_prior:*` rows accumulate (one per overwrite) with no retention policy yet.

## Alternatives Considered

- **Option B — a separate, monitoring-owned writer** (a `monitoring` repo/service that updates `scholarships` directly) — rejected: it violates FR-MON-4's "no divergent data path". Two writers would need to be kept consistent on normalization, enum coercion, failure logging, and provenance, and would let monitoring bypass the ingestion workflow's validation.
- **Reuse the conflict-resolution merge path (append `scholarship_fields` conflict rows only)** — rejected: per ADR-0002 the scalar column would never change, so the stored deadline would stay stale, defeating change detection; and an unconfirmed listing value would lose to the stored official one, so "conflicting" rows would pile up on every run.
- **Encode the prior value with `value_status="conflicting"` rows** — rejected: readers are told to treat `conflicting` as an unresolved disagreement; a historical value is not one.
- **Overwrite without an audit row** — rejected: silently losing a verified value is unrecoverable.

## References

- Feature Spec: `specs/002-source-monitoring-hardening/spec.md` (FR-MON-3, FR-MON-4)
- Implementation Plan: `specs/002-source-monitoring-hardening/plan.md`
- Related ADRs: ADR-0002 (ingestion dedup merge does not reconcile scalar columns — unchanged outside this path)
- Related code/tests: `backend/app/workflows/ingestion/graph.py`, `backend/app/services/listing_diff.py`, `backend/tests/workflows/test_ingestion_update_path.py`, `backend/tests/workflows/test_source_monitor.py`
