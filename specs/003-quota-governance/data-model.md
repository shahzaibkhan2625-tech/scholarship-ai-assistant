# Data Model: LLM Quota Governance & Cheap Monitoring (003)

All changes are **additive**. No existing column is altered, widened, renamed or dropped; no existing row is rewritten. Table and column names below are proposals for the migration; types follow existing conventions (UUID keys, timezone-aware timestamps, non-native enums stored as short strings).

## New table: `llm_budget_ledger`

One row per (budget day, request type, priority class, event). A counter table: every FR-OBS-1 number is a sum over it.

| Column | Type | Notes |
|---|---|---|
| `budget_day` | `date` | Calendar day in the configured budget timezone (default `America/Los_Angeles`) |
| `request_type` | `varchar(16)` | `generate` or `embed` |
| `priority_class` | `varchar(24)` | `user_facing`, `backfill`, `monitoring`, `validation` |
| `event` | `varchar(24)` | `allowed`, `deferred`, `denied`, `provider_exhausted`, `discrepancy`, `avoided_unchanged`, `avoided_rules` |
| `count` | `integer` NOT NULL DEFAULT 0 | Incremented atomically with `INSERT ... ON CONFLICT DO UPDATE` |
| `updated_at` | `timestamptz` | |

Primary key: (`budget_day`, `request_type`, `priority_class`, `event`).

- "Used today" for a request type = sum of `count` where `event = 'allowed'`.
- `discrepancy` is written when the provider reports exhaustion while the local `allowed` total is below the allowance.
- Rows older than the retention window (default 90 days) are pruned by the existing daily retention job.

## New table: `llm_budget_state`

Provider-imposed pause state, one row per request type.

| Column | Type | Notes |
|---|---|---|
| `request_type` | `varchar(16)` PK | |
| `paused_until` | `timestamptz` NULL | Non-NULL and in the future means no non-user-facing calls and no user-facing provider calls |
| `pause_reason` | `varchar(64)` NULL | `daily_quota`, `rate_limit`, `provider_error` |
| `pause_set_at` | `timestamptz` NULL | |
| `observed_limit` | `integer` NULL | The limit the provider reported (for example 20), shown in metrics so the configured allowance can be checked against reality |

## New table: `source_check_state`

Per monitored source runtime state for story 1 (change detection), story 3 (cadence back-off) and story 5 (rule review). One row per source; created on first check; bootstrapped from fetch history when absent (research R-08).

| Column | Type | Notes |
|---|---|---|
| `source_id` | `uuid` PK, FK to `source_registry.id` | |
| `fingerprint` | `varchar(64)` NULL | SHA-256 hex of the normalised visible text at the last **successful** extraction |
| `fingerprint_at` | `timestamptz` NULL | When that extraction happened |
| `etag` | `text` NULL | Conditional-request validator, only if the server supplied one |
| `last_modified` | `text` NULL | Same |
| `validators_trusted` | `boolean` NOT NULL DEFAULT false | Set true only after the server proved it honours them (research R-06) |
| `last_extraction_outcome` | `varchar(24)` NULL | `ok`, `rules`, `zero_candidates`, `provider_quota`, `provider_error`, `provider_parse`, `deferred` |
| `last_full_extraction_at` | `timestamptz` NULL | Drives the forced full re-check (FR-SKIP-5) |
| `backoff_level` | `integer` NOT NULL DEFAULT 0 | Doubling back-off for an unchanged page that keeps failing |
| `next_attempt_at` | `timestamptz` NULL | If in the future the source is not due, regardless of its interval |
| `checks_window` | `integer` NOT NULL DEFAULT 0 | Checks in the current noise window |
| `changed_window` | `integer` NOT NULL DEFAULT 0 | Of those, how many saw a changed fingerprint (noisy when high, FR-SKIP-7) |
| `rules_review_needed` | `boolean` NOT NULL DEFAULT false | Raised when rules match nothing or produce an anomalous count (FR-RULES-3/4) |
| `rules_review_reason` | `varchar(64)` NULL | `no_match`, `count_dropped`, `count_jumped` |
| `updated_at` | `timestamptz` | |

State transitions:
- Successful extraction (LLM or rules): set `fingerprint`, `fingerprint_at`, `last_full_extraction_at`, `last_extraction_outcome`, reset `backoff_level` and `next_attempt_at`.
- Unchanged and eligible to skip: no change to `fingerprint`; increment `checks_window`.
- Changed fingerprint: increment `checks_window` and `changed_window`; extraction proceeds.
- Zero grounded candidates on an unchanged page: increment `backoff_level`, set `next_attempt_at = now + min(60 min * 2^level, cadence interval)`.
- Deferred by budget or pause: set `next_attempt_at` to the retry time; do **not** touch the fingerprint.

## Changed table: `source_fetch_log`

| Change | Detail |
|---|---|
| Add `outcome` | `varchar(24)` NULL, no default, no index. NULL on all existing and all ordinary rows. Values listed in research R-10. |

`status` keeps its three values and its `VARCHAR(7)` length. No CHECK constraint exists on it today, and none is added.

Readers that must change behaviour (code, not schema): the shared failure-streak definition `ranked_monitoring_runs` (`data/repositories/source_repo.py:338`, used by health and retention) and the failed-source counting in `services/coverage.py:63-75` both ignore rows whose `outcome` starts with `provider_`. Readers keyed on `status = ok AND items_found IS NOT NULL` (`official_fetch.py:259`, `source_monitor/graph.py:171`, `services/retention.py:72`) are unaffected because the new success-type rows leave `items_found` NULL.

## Changed table: `monitoring_runs`

| Change | Detail |
|---|---|
| Add `sources_skipped` | `integer` NOT NULL DEFAULT 0 (checks skipped as unchanged) |
| Add `sources_deferred` | `integer` NOT NULL DEFAULT 0 (checks deferred for budget or pause) |

Both are metadata-only additions on current PostgreSQL (constant default), so there is no table rewrite.

## No schema change needed

- **Per-source interval override and deterministic rules**: stored in the existing `source_registry.extraction_rules` JSONB under namespaced keys (`check_interval_minutes`, `deterministic`), validated by the registry write schema. Human-governed configuration stays on the registry row (constitution Principle IV).
- **Last checked and last success**: already on `source_registry` (`last_checked_at`, `last_success_at`).
- **Allowance, reserve, ladder offsets, backfill share, cadence defaults, forced-recheck days, back-off base, noise threshold, anomaly thresholds, budget timezone**: new settings in `core/config.py`, no storage.

## Entity map to the spec

| Spec entity | Realised as |
|---|---|
| LLM Budget | `llm_budget_ledger` + `llm_budget_state` + settings |
| Priority Class | `priority_class` values; context manager in `core/llm_gate.py` |
| Budget Event | `event` rows in the ledger (counts) |
| Content Fingerprint | `source_check_state.fingerprint` and validators |
| Check Schedule | `source_check_state.next_attempt_at` + `source_registry.last_checked_at` + derived interval |
| Provider Error Outcome | `source_fetch_log.outcome = provider_*` |
| Extraction Rule Set | `source_registry.extraction_rules['deterministic']` + `source_check_state.rules_review_*` |
