# Quickstart: Source Monitoring, Coverage Analytics & Production Hardening

Exercises each user story's acceptance scenarios against the contracts in `contracts/openapi.yaml` and the data model in `data-model.md`. Assumes the 001 environment already works (`uv sync`, `alembic upgrade head`, a running Postgres per `DATABASE_URL`).

## 0. Apply this feature's migrations

```bash
cd backend
uv run alembic upgrade head   # includes the two new 002 migrations (source_registry/source_fetch_log/monitoring_runs columns+table, then alerts/alert_preferences/candidate_source_validations)
```

## US1 — Scheduled monitoring catches a changed scholarship

1. Seed one `active` `source_registry` row with `listing_page_url` set, and one stored `scholarships` row sourced from it (`scholarship_sources.source_id` pointing at the seeded source).
2. Point that source's fetch (test double / recorded fixture under `tests/fixtures/`) at content with: one new scholarship, one changed deadline on the existing one.
3. `POST /monitoring/runs` (manual trigger) instead of waiting for the schedule.
4. Assert: `GET /monitoring/runs/{run_id}` shows `status=completed`, `changes_detected=2`; the new scholarship has `lifecycle_status=newly_discovered`; the existing scholarship's `lifecycle_status=updated` and its prior deadline is still visible via its `scholarship_fields`/conflict-resolution trail (never overwritten silently).
5. Re-run with no content change → `changes_detected=0`, `source_registry.last_checked_at` advances, no new scholarship/alert rows (US1 Acceptance Scenario 5).

## US2 — Re-match and alert the affected owner

1. Seed a profile whose hard constraints are satisfied by the new scholarship from US1 step 2.
2. After the monitoring run, check `GET /alerts` for that user: one alert referencing the new scholarship, `channels_requested` containing both `in_app` and `email` (default `alert_preferences`).
3. Flip `PUT /alerts/preferences` to `email_enabled=false`, trigger a second qualifying change, and confirm the resulting alert's `channels_requested=["in_app"]` only, and no `EmailChannel.send` call was attempted.
4. Seed a change that affects no owner's eligibility/saved scholarships → confirm zero new `alerts` rows (FR-ALERT-6).

## US3 — See source health, not silent failure

1. Configure one source's fetch fixture to raise (simulated timeout) and another to succeed with zero changes.
2. Trigger a run; `GET /sources/health` must show the first as `status=failing` (or `active` with a non-zero `consecutive_failures` before the N-failure threshold) with a non-null `last_checked_at` and `last_success_at` unchanged from before — never presented as "0 scholarships found."
3. The second source's `GET /sources/{id}/fetch-log` entry shows `status=ok`, `items_found=0` — visibly distinct from the failing source's `status=fail` entry.

## US4 — Coverage analytics

1. Seed a mix of active/failing/pending-candidate sources across ≥2 countries.
2. `GET /coverage` — assert `sources_configured`/`sources_active`/`sources_checked`/`sources_failed`/`candidate_sources_pending` match the seed exactly, `claims_complete_coverage` is `false`, and at least one gap string exists for any (country, source_type) combo with no healthy active source.

## US5 — Lifecycle state at a glance

Walk one seeded scholarship through: seed as `newly_discovered` → run monitoring with official confirmation → `verified` → change a field → `updated` → advance the clock past `freshness_window_days` with no successful re-check → `stale` → force fetch failures past retry exhaustion → `source_unavailable` → simulate the source listing it closed → `closed` → simulate it listed open again → `reopened` (never back to `newly_discovered`). Assert via `GET /scholarships/{id}` (001 endpoint, unchanged) at each step that exactly one `lifecycle_status` value is present.

## US6 — Monitoring and discovery fetch the right page

1. Seed a source with `listing_page_url` different from its `domain` homepage.
2. Trigger monitoring and (001's) on-demand discovery against it; both resulting `source_fetch_log` rows' `fetched_url` equal the configured listing page, `used_homepage_fallback=false`.
3. Clear `listing_page_url` to `NULL`, re-run: `fetched_url` is the homepage, `used_homepage_fallback=true`.
4. Change `listing_page_url` to a third URL, re-run: the next fetch uses the new URL immediately (FR-FETCH-2).

## US7 — Candidate-source pre-checks before human approval

1. Let a discovery/monitoring run surface a new candidate (001's existing `record_or_bump_candidate_source` path, unchanged).
2. Confirm `GET /sources/candidates/{id}/validations` has at least one row with `reachable`/`extractable`/`official_signals` populated.
3. Confirm the candidate cannot be used as a fact source (no `source_registry` row exists for it) regardless of whether all checks passed.
4. `POST /sources/candidates/{id}/approve` (001's existing endpoint, unchanged) with a candidate that *failed* a check — must succeed (automated checks never override human judgment, FR-CANDVAL-4).

## US8 — Production hardening

1. **HTTPS**: with the `caddy` sidecar running (`docker-compose.podman.yml`), confirm `http://<host>/health` redirects/upgrades to `https://`.
2. **Rate limit**: burst >N requests/minute against any endpoint from one client IP; confirm a `429` with a clear body after the threshold, and that a second client IP is unaffected.
3. **Retry/backoff**: force a transient fetch failure (fails twice, succeeds third attempt) and confirm exactly one `source_fetch_log` row with `retry_count=2`, `status=ok`.
4. **Logs/metrics**: trigger a monitoring run; confirm a structured JSON log line exists for it and `GET /metrics` exposes a counter that incremented.
5. **Document persistence**: upload a document (001 endpoint), restart the `backend` container (`podman restart`), confirm the document is still retrievable — proves the volume mount, not ephemeral storage.
6. **Retention**: seed `source_fetch_log` rows older than `FETCH_LOG_RETENTION_DAYS` for a source that also has fewer than `HEALTH_WINDOW_N` total rows; run the retention job; confirm those old rows are **not** deleted (the "preserve enough to explain current health" edge case) — then seed enough rows to exceed `HEALTH_WINDOW_N` and confirm the oldest excess rows beyond the window *are* pruned once also past the retention period.
7. **Backups**: document-only check — confirm Neon's point-in-time-recovery is enabled for the project (console/API check, no app code involved).
8. **Security**: run `uv run pip-audit` in CI and confirm zero known-critical findings; confirm no secret/credential literal exists outside `.env`/`.env.example` placeholders (`git grep` for the obvious patterns).

## New environment variables (add to `.env.example`, all optional with safe defaults so CI needs no new secrets)

```
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
ALERTS_FROM_EMAIL=
MONITORING_INTERVAL_MINUTES=60
FETCH_LOG_RETENTION_DAYS=90
HEALTH_WINDOW_N=5
RATE_LIMIT_PER_MINUTE=120
STORAGE_ROOT=/data/storage   # unchanged from 001; documented here as the volume-mount target for NFR-HARDEN-6
```
