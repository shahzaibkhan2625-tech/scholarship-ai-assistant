# Phase 1 Data Model: Source Monitoring, Coverage Analytics & Production Hardening

All changes here are **additive** to `specs/001-scholarship-mvp/data-model.md`. No existing table is dropped, renamed, or has a column removed/retyped; no existing enum value is removed. Every change ships as a new Alembic migration (or migrations) layered on top of the existing chain (`fed0167c65c9 -> 6e1035fe0cc6 -> 603848a52d5b -> d501fadbb3f1 -> b53c6b5689ba -> ad4826bd57e6`), never editing a prior migration file. Value-status vocabulary and `user_id`-scoping discipline are unchanged from 001 and are not repeated here except where a new table needs to state its scoping explicitly.

---

## 1. `source_registry` — additive columns (spec Key Entity: Source Registry Entry, extended)

| Field | Type | Notes |
|---|---|---|
| `listing_page_url` | text, nullable | FR-FETCH-1/2. The specific page scholarships are listed on, distinct from `domain`'s homepage. `NULL` on every existing row (safe — triggers the existing homepage-fallback path, FR-MON-7). An operator sets this when registering/editing a source; `fetch_and_extract_listing` prefers it over the bare domain when present. |
| `freshness_window_days` | integer, nullable | FR-LIFECYCLE-2. Per-source re-verification window; `NULL` means "use the service-level default" (`services/lifecycle.py`'s `DEFAULT_FRESHNESS_WINDOW_DAYS`), consistent with spec Assumptions ("per-source freshness windows ... operational configuration, not fixed spec values"). |

**Validation rules**: unchanged existing rule (`get_active_sources`/`get_source_by_id` gate on `status == active`) continues to apply; the two new columns are read-only inputs to monitoring/lifecycle logic, never gating fields themselves.

**State transitions**: unchanged (`status`: `active ⇄ disabled`, plus `failing`; promotion `failing -> disabled` remains operator-only, not automated — same as 001).

---

## 2. `source_fetch_log` — additive columns (spec Key Entity: Source Fetch Outcome, extended)

| Field | Type | Notes |
|---|---|---|
| `fetched_url` | text, nullable | FR-FETCH-3. The URL actually fetched for this attempt (listing page or homepage-fallback) — makes listing-vs-homepage fetches auditable. `NULL` on pre-002 rows (no regression; those rows predate the listing-page concept entirely). |
| `used_homepage_fallback` | boolean, not null, `server_default=false` | FR-MON-7. `true` exactly when `source_registry.listing_page_url` was `NULL` (or unreachable-by-config) at fetch time and the domain homepage was used instead. Existing rows default to `false`, which is correct (001 always fetched the domain directly; there was no listing-page concept to fall back from). |
| `monitoring_run_id` | UUID, nullable, FK -> `monitoring_runs.id` | Attributes a fetch-log row to a specific scheduled run; `NULL` means the row came from on-demand discovery (exactly what every pre-002 row means, so no backfill is needed — their `NULL` is simply true). |

**Validation rules**: unchanged (every fetch attempt, success or failure, still gets exactly one row — FR-HEALTH-1 — this was already true in 001 and 002 adds columns, not a new write path).

---

## 3. `scholarships.lifecycle_status` — **no schema change** (spec Key Entity: Scholarship Lifecycle State)

Already a single non-nullable `Enum(LifecycleStatus, native_enum=False)` column covering exactly the nine spec values (`backend/app/models/scholarship.py`). FR-LIFECYCLE-1 ("exactly one of the nine states, impossible to represent two") is satisfied by the existing column type — verified by reading the model, not assumed. 002 adds transition **logic** only:

- `services/lifecycle.py` (new module) exposes `evaluate_freshness(scholarship, source, now) -> LifecycleStatus | None` and `evaluate_fetch_outcome(scholarship, fetch_outcome) -> LifecycleStatus | None`, each returning `None` when no transition applies (never forcing a write when nothing changed — satisfies spec US1 Acceptance Scenario 5: "no spurious change records").
- Transition rules encoded as data, not scattered conditionals, mirroring `services/verification.py`'s existing `compute_lifecycle_status` style:
  - `* -> stale`: freshness window (per-source `freshness_window_days`, or module default) elapsed without a successful re-check (FR-LIFECYCLE-2).
  - `* -> source_unavailable`: retries exhausted across the configured retry policy (`sources/retry_policy.py`, reused unchanged) — never `expired`/`closed`, which require *confirmed* source content (FR-LIFECYCLE-3).
  - `closed -> reopened` (never `-> newly_discovered`) when a monitoring fetch finds a previously-closed scholarship listed open again (FR-LIFECYCLE-4).
  - Every transition above is driven by `source_monitor`/ingestion outcomes only — no LLM call anywhere in `services/lifecycle.py` (FR-LIFECYCLE-5, Principle II).

---

## 4. `monitoring_runs` — new table (spec Key Entity: Monitoring Run)

System-owned, no `user_id` (same reasoning as `source_registry`/`source_fetch_log`: a monitoring run is not scoped to an end user).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `started_at` | timestamptz, not null, `server_default=now()` | |
| `ended_at` | timestamptz, nullable | `NULL` while `status = running`. |
| `trigger` | enum(`scheduled`, `manual`) | FR-MON-1 runs are `scheduled`; an operator-triggered run (useful for `quickstart.md`'s manual test path) is `manual`. |
| `status` | enum(`running`, `completed`, `failed`) | `failed` means the run itself errored out (not merely that one source's fetch failed — individual source failures are `source_fetch_log` rows and do not fail the run). |
| `sources_processed` | integer, not null, default 0 | |
| `sources_failed` | integer, not null, default 0 | |
| `changes_detected` | integer, not null, default 0 | Count of scholarship records created/updated/transitioned by this run — the basis for SC-001's "results in the affected scholarship record being updated" check. |

**Validation rules**: `ended_at` is set exactly once, when `status` moves out of `running`; a run row is never deleted by the retention policy (Decision 7 / NFR-HARDEN-7) — only `source_fetch_log` rows age out, because `monitoring_runs` rows are the small, low-volume summary the retention policy's "preserve enough to explain current state" edge case depends on.

**State transitions**: `running -> completed | failed`, one-directional, no further transitions.

---

## 5. `alerts` — new table (spec Key Entity: Alert)

User-owned (`user_id` FK, directly scoped — constitution §19 discipline).

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK -> `users.id`, not null, indexed | |
| `scholarship_id` | UUID FK -> `scholarships.id`, not null, indexed | |
| `match_id` | UUID FK -> `matches.id`, nullable | The match verdict (if any) that triggered this alert — `NULL` is valid (e.g. a changed-details alert that isn't itself eligibility-driven). |
| `monitoring_run_id` | UUID FK -> `monitoring_runs.id`, nullable | Which run detected the change that produced this alert; `NULL` for a hypothetical future non-monitoring-triggered alert (not used by 002, kept nullable for honesty about what this FK means). |
| `change_summary` | JSONB, not null | What changed (`{"kind": "new_match" \| "eligibility_changed" \| "details_changed", "fields": [...], ...}`) — FR-ALERT-3's "what changed" content, shown in-app and in the email body. |
| `channels_requested` | JSONB (list of str), not null | Snapshot of which channels were attempted (`["in_app", "email"]`), taken from `alert_preferences` at generation time — auditable even if preferences change later. |
| `delivered_in_app_at` | timestamptz, nullable | Set by `InAppChannel.send`. |
| `delivered_email_at` | timestamptz, nullable | Set by `EmailChannel.send`; `NULL` + a non-null `email_error` means delivery was attempted and failed (FR-ALERT-4, Edge Cases: "alert failure ... does not silently disappear"). |
| `email_error` | text, nullable | |
| `created_at` | timestamptz, not null, `server_default=now()` | |

**Validation rules**: an `alerts` row is only ever created when FR-ALERT-3's condition holds (newly-favorable or materially-changed match outcome) or FR-ALERT-6's negative is checked first (no row at all for a non-impacting change) — enforced in `services/alerts.py`, not by a DB constraint (same "code-level invariant, test-matrix verified" treatment 001's data-model.md already uses for the hard-constraint verdict rule).

**State transitions**: append-only; a delivery retry (Edge Cases: alert failures are "retried or surfaced") updates `delivered_email_at`/`email_error` on the same row rather than creating a duplicate alert for the same detected change.

---

## 6. `alert_preferences` — new table (supports FR-ALERT-5)

User-owned, one row per user.

| Field | Type | Notes |
|---|---|---|
| `user_id` | UUID PK, FK -> `users.id` | One row per user; absence of a row means "use defaults" (both channels enabled), so no migration-time backfill is required for existing users. |
| `in_app_enabled` | boolean, not null, default true | |
| `email_enabled` | boolean, not null, default true | |
| `updated_at` | timestamptz, not null, `server_default=now()` | |

**Validation rules**: disabling both channels never stops monitoring/re-matching/record updates (FR-ALERT-5) — `source_monitor` and `services/matching.py` never read this table; only `services/alerts.py`'s dispatch step does.

---

## 7. `candidate_source_validations` — new table (spec Key Entity: Candidate Source Validation Result)

No `user_id` (governance data, same scoping class as `candidate_sources` itself). Append-only history, not an overwrite-in-place row, so a reviewer can be shown recency rather than a single mutable "current" result going stale silently (Edge Cases: "automated checks are re-run (or their recency is shown)").

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `candidate_source_id` | UUID FK -> `candidate_sources.id`, not null, indexed | |
| `checked_at` | timestamptz, not null, `server_default=now()` | |
| `reachable` | boolean, not null | FR-CANDVAL-1. |
| `reachable_detail` | text, nullable | HTTP status / error, mirrors `source_fetch_log.error`'s honesty. |
| `extractable` | boolean, not null | FR-CANDVAL-1. |
| `extractable_detail` | text, nullable | e.g. "0 grounded candidates extracted." |
| `official_signals` | JSONB, not null, default `{}` | Heuristic findings (domain-pattern match, keyword match, stated-affiliation match) — advisory only, never a pass/fail gate (FR-CANDVAL-2/4). |

**Validation rules**: this table is never joined into any authoritative scholarship data path (same rule as `candidate_sources` itself) and never, by itself, changes `candidate_sources.status` — only `source_repo.approve_candidate_source`/`reject_candidate_source` (existing, unchanged, human-gated) do that (FR-CANDVAL-2/3).

**State transitions**: none (append-only; "current" = the latest row by `checked_at` for a given `candidate_source_id`).

---

## Cross-cutting notes

- **Retention** (NFR-HARDEN-7): applies to `source_fetch_log` only (the one genuinely high-volume table here), per `services/retention.py`'s "delete older than N days except the most recent `HEALTH_WINDOW_N` rows per source" rule (research.md Decision 7). `monitoring_runs`, `alerts`, `alert_preferences`, and `candidate_source_validations` are all low-volume, human/operationally-relevant history and are explicitly **not** pruned by this feature.
- **Isolation**: `alerts` and `alert_preferences` carry `user_id` directly, consistent with 001's "every user-owned table carries `user_id` from day one" rule; `monitoring_runs`, the `source_registry`/`source_fetch_log` additions, and `candidate_source_validations` are system/governance-owned, consistent with how `source_registry`/`source_fetch_log`/`candidate_sources` were already classified in 001.
- **Migrations**: proposed as two migrations to keep review small — (a) additive columns on `source_registry`/`source_fetch_log` + `monitoring_runs` (needed together: the FK), (b) `alerts` + `alert_preferences` + `candidate_source_validations`. Exact split/order is a `/sp.tasks` concern, not decided further here.
