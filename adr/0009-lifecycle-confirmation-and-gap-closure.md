# ADR-0009: Monitoring Confirms Records Through a Narrow Repo Path, `newly_discovered` Exits on Re-sighting, and `updated` Beats the Verified Exit

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-13
- **Feature:** 002-source-monitoring-hardening (US5 — lifecycle states, FR-LIFECYCLE-1..5, tasks T212–T215)
- **Context:** US1–US3 already built the lifecycle machinery (`services/lifecycle.py`, the monitor's `_write_lifecycle`, D2's gated absence-to-`closed`, the Option A `expired` lift). Reading the code for US5 showed the remaining gaps were not wiring but missing premises:
  1. **Nothing in monitoring could ever make a record `verified`.** Rules 6/7 read `verification_status == VERIFIED`, but the monitor deliberately ingests with `official_source_confirmed=False` and `mark_seen_verified` only refreshes `last_verified_at`. `verification_status` became `VERIFIED` only inside ingestion. US5 Scenario 4 (`unverified` → `verified` on a confirming fetch) was unreachable in production.
  2. **`newly_discovered` never exited.** No rule covered it, so a record re-seen open stayed `newly_discovered` forever, yet the quickstart walk expects `verified`.
  3. **Absent `stale` / `source_unavailable` records could be stuck.** After a successful grounded fetch, a record missing from the listing (and not closeable under D2) received no outcome and kept a state premised on a failed fetch.
  4. **A field change and a confirming re-sighting can coincide** on one check (e.g. a `stale` record whose deadline moved), and two rules then name different targets.

## Decision

1. **Confirmation rule (G1).** A *matched, grounded, open* listing item from an approved registry source counts as "confirms against its official source".
   - It is applied by a new, narrow repo function, `scholarship_repo.mark_confirmed_by_monitoring(db, ids)`, shaped like `mark_seen_verified`: it sets `verification_status = VERIFIED` for the given ids **only where it is currently `UNVERIFIED`**. `CONFLICTING` rows are never touched (a disagreement needs resolution, not a re-sighting) and already-`VERIFIED` rows are a no-op.
   - The monitor calls it per matched candidate whose listed state is open, **before** the transition is evaluated, so the pure lifecycle rules see the confirmation. An explicit-`closed` signal never confirms.
   - **Ingestion's `official_source_confirmed=False` default is not changed.** That flag also drives provenance rows (`ScholarshipSource.verification_status` / `verified_at`), `last_verified_at` at creation, conflict resolution and the overwrite semantics of ADR-0006. Flipping it for monitoring would silently re-weight those for every monitored scholarship (a listing could then overwrite stronger data on "official" authority, the exact trade-off ADR-0006 bounded with the audit row). The confirmation here is one column on one already-matched row; the blast radius is that column.
2. **`newly_discovered` exit (G2).** The pure service gains rule 6b: `newly_discovered` plus a grounded, open sighting leaves to `verified` when `verification_status` is `VERIFIED` and `unverified` otherwise — the same form as the O-3 exit. Combined with decision 1, a re-sighted record from an approved source lands on `verified`; a `CONFLICTING` one lands on `unverified`. A first sighting (ingestion creating the row) is not a re-sighting and stays `newly_discovered`.
3. **Stuck-state recovery (G3).** The pure service gains rule 9: a successful, grounded fetch with `listed_state=None` (absent, D2 gate not met) returns `stale` / `source_unavailable` to `unverified` — the source is reachable again and nothing about the record was confirmed. To avoid a pointless two-write flap, the sweep previews freshness: a record already past its window goes straight to (or stays) `stale`. A stuck-state recovery is **not** counted as a listing change (`changes_detected` counts only `closed`).
4. **Precedence (Q4): `updated` wins over the verified exit.** When a field change and a confirming re-sighting occur in the same check, the record becomes `updated`; the verified exit is not applied. `verification_status` is still confirmed, so the next unchanged sighting moves nothing it should not. This is pinned by `test_updated_wins_over_the_verified_exit_when_a_field_change_and_resighting_coincide` so it cannot flip silently.
5. **Clock seam (G6).** `run_source_monitor(..., now: Callable[[], datetime] | None = None)`; `None` keeps `datetime.now(timezone.utc)`. It feeds the freshness refresh, the stale sweep and the Option A `expired` re-derivation, letting the walk test advance time without back-dating rows.
6. **Read surface (Q2).** `GET /scholarships/{id}` (bearer auth, read-only, 404 on unknown id) returns `id, name, lifecycle_status, verification_status, last_verified_at, deadline`, added to the 002 contract additively. It did not exist in 001 despite the task text assuming it.

## Consequences

### Positive

- US5 Scenario 4 and the walk are reachable in production; every transition remains deterministic and LLM-free (FR-LIFECYCLE-5).
- No migration; no change to ingestion, provenance rows or ADR-0006 overwrite semantics.
- The pure service keeps a single table of rules; the graph keeps a single lifecycle writer (`_write_lifecycle`, guarded by an AST test).

### Negative

- **Trust is now stated, not inherited:** any approved registry source listing a record open verifies it. A wrong listing on an approved source therefore yields `verified` for that record (Principle IV already makes the registry the trust boundary; the listing carries no stronger signal). `CONFLICTING` rows are the only exemption.
- A field change on a `stale` / `source_unavailable` / `newly_discovered` record reports `updated`, not `verified`, until the next unchanged sighting (decision 4); `updated` is itself freshness-eligible.
- A stale record absent from the listing cycles through no state at all (the preview prevents the flap), but the check costs one in-memory evaluation per absent stuck record per run.

### Known gaps (named, deferred)

- **`expired` is not time-driven.** A record whose deadline simply passes is not moved to `expired` by monitoring; only ingestion-time derivation and the Option A lift touch it. Not required by FR-LIFECYCLE-1..5.
- `by_name` matching (normalised name) is unchanged: two stored rows with the same normalised name still resolve to the most recent.

## Alternatives Considered

- **Pass `official_source_confirmed=True` from the monitor.** Rejected: widens the blast radius over provenance, conflict resolution and ADR-0006's overwrite semantics (see Decision 1).
- **Treat the first sighting as the confirmation.** Rejected: ingestion creates the row unconfirmed on purpose; only a *re*-sighting of an approved listing is evidence beyond the creating one.
- **Let `verified` win over `updated`.** Rejected: it would hide a real change from the one-state-at-a-glance view.
- **Leave stuck records and document it.** Rejected: `source_unavailable` after recovery would persist for any record the listing no longer names, contradicting its meaning.

## References

- Spec: `specs/002-source-monitoring-hardening/spec.md` (US5, FR-LIFECYCLE-1..5, O-3)
- Tasks: T212–T215
- Related ADRs: ADR-0006 (monitoring update path), ADR-0008 (source health; transaction-scoped lock design unchanged)
