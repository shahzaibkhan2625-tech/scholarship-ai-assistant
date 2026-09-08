# ADR-0002: Ingestion Dedup Merge Does Not Reconcile Scalar Columns

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-09-09
- **Feature:** 001-scholarship-mvp (T087 — Ingestion workflow dedup/merge fix)
- **Context:** T087's ingestion workflow (`backend/app/workflows/ingestion/graph.py`) computed a `dedup_result` (T081, Blueprint §31) via the deterministic name+university+intake key or vector-similarity threshold, but the STORE node never acted on it — a record matching an already-persisted scholarship was inserted as a second, duplicate `scholarships` row instead of being merged. Fixing this required deciding what "merge" means for a `scholarships` row that has both first-class scalar columns (e.g. `degree_level`, `funding_status`, `deadline`) and a separate `scholarship_fields` table used for per-field provenance/conflict tracking (T083, Blueprint §32).

## Decision

When `_store_node` resolves a dedup match against a real, persisted `Scholarship` row (`_resolve_dedup_match`), the merge is scoped to two things only:

1. A new `ScholarshipSource` row is added, pointing the new source/provenance at the existing `scholarship_id` — no new `scholarships` row is inserted.
2. Any field disagreements already surfaced by `derive_conflicts`/`conflict_resolution` (T083) are appended as new `scholarship_fields` rows on the *existing* scholarship, and `verification_status` is set to `CONFLICTING` if any resolution is conflicting.

First-class scalar columns on `scholarships` (`degree_level`, `funding_type`, `deadline`, etc.) are **never rewritten** by a merge. This mirrors the pre-existing single-insert path, which also never rewrites those columns from conflict resolutions — only appends `scholarship_fields` rows.

## Consequences

### Positive

- No risk of a later, lower-quality source silently overwriting a scalar column that an earlier, higher-reliability source already populated — the existing conflict-resolution service (official > recency > reliability) still adjudicates at the `scholarship_fields` level, and the scalar column is left as originally set.
- Avoids re-triggering `_record_completeness`/`_missing_flagged_fields` bookkeeping on every repeated merge, which would otherwise accumulate duplicate completeness markers per merge event.
- Keeps the merge path small and low-risk: it only ever appends rows (`scholarship_sources`, `scholarship_fields`), never mutates the `scholarships` row's scalar state — easy to reason about and to test (see `test_two_ingestion_runs_for_the_same_scholarship_from_different_sources_merge_into_one_row` in `backend/tests/workflows/test_ingestion.py`).

### Negative

- **Known limitation:** a scalar column can go stale relative to `scholarship_fields`. If a merge produces a `value_status="conflicting"` field record, the scalar column still reflects whichever value was set at original insert time — it does not reflect the winning/latest resolution. Any UI/API layer reading a scholarship **must consult `scholarship_fields` for `value_status='conflicting'` fields** rather than trusting the scalar column alone as ground truth.
- This is a known gap to revisit if/when scalar-column reconciliation is needed — most likely Phase 3/4 application-prep display logic, where a user-facing view needs the single best current value per field, not just the original insert-time snapshot.

## Alternatives Considered

- **Rewrite scalar columns from conflict-resolution winners on every merge** — rejected for now: conflates "record provenance/conflict tracking" (owned by `scholarship_fields`) with "authoritative display value" (the scalar column), and requires deciding a full reconciliation strategy (which fields, when, how `_record_completeness` reacts) that is out of scope for the T087 bug fix. Deferred rather than designed under time pressure.
- **Block merge entirely when conflicting fields are detected, forcing manual review before storage** — rejected: would regress the dedup fix's core goal (no duplicate rows) whenever any field disagrees, which conflict-resolution is explicitly designed to handle automatically in the non-tied case.

## References

- Feature Spec: `specs/001-scholarship-mvp/spec.md`
- Implementation Plan: `specs/001-scholarship-mvp/plan.md`
- Blueprint: `specs/scholarship-ai-assistant-master-blueprint-v1.md` §31, §32
- Related ADRs: [ADR-0001](0001-source-registry-governance-and-seed-sources.md)
- Related code: `backend/app/workflows/ingestion/graph.py` (`_resolve_dedup_match`, `_store_node`), `backend/tests/workflows/test_ingestion.py`
