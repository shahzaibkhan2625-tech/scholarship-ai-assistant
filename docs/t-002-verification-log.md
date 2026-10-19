# T248 — Feature 002 Verification Log

Executes `specs/002-source-monitoring-hardening/quickstart.md` (US1–US8) and records pass/fail
per step, including what could **not** be demonstrated. Written at the end of US8 batch B4.

- **Date:** 2026-10-14
- **Branch:** `002-source-monitoring-hardening` (base `cb0af14` + the uncommitted B4 working tree)
- **Environment:** developer laptop (Windows 10), Python 3.12, `uv`; Neon **pooled** Postgres via
  `DATABASE_URL`; schema at Alembic head `f2b8d4a6c1e3` (`alembic current` = `alembic heads`, a single head).
- **Not the VM.** Nothing below was run on the Oracle VM, in a container, or against a public address.

## How to read this log

The quickstart's US1–US7 scenarios are written against **fixtures and test doubles** ("point that
source's fetch at a recorded fixture", "simulated timeout"). They were therefore executed as the
repository's automated tests, not by hand against a live service. Each row names the tests that
cover the step; "PASS" means those tests passed in the full-suite run below. This is
automated-fixture evidence, not a live end-to-end demonstration.

## Run evidence

| Check | Command | Result |
|---|---|---|
| Full backend suite (T247) | `uv run pytest -q -p no:cacheprovider -rfE --durations=10` | **1015 passed, 5 deselected, 0 failed**, 1h33m39s (`backend/full_us8_b4.txt`, scratch/ignored) |
| Deselected tests | `-m smoke` (5 tests, `tests/smoke/`) | **Not run** — see open item 1 |
| Scored eval gate | `uv run python -m evals.runner` | matching_correctness 1.000 (≥1.0) PASS; qa_groundedness 1.000 (≥0.5) PASS; generation_groundedness 1.000 (≥1.0) PASS; exit 0 |
| Dependency audit | `uv run pip-audit --strict --ignore-vuln CVE-2026-85394 --ignore-vuln PYSEC-2026-1325` | "No known vulnerabilities found, 3 ignored" (the two documented ignores expand to three advisory entries); exit 0 |
| Retention dry run | `uv run python -m app.services.retention --dry-run` | `0 row(s) would be deleted` against the development Neon DB (nothing is older than the 90-day cutoff yet) — read-only |
| Migration state | `uv run alembic current` / `heads` | both `f2b8d4a6c1e3` (head) |
| New in B4 | `tests/infra/test_deploy_config.py` (13), `tests/api/test_openapi_contract_002.py` (10), `tests/workflows/test_source_monitor_guardrails.py` (4) | all passed (included in the 1015) |

The 001 tests (matching matrix, discovery guardrails, coverage, etc.) are part of the same run and
unchanged; none failed. `specs/001-scholarship-mvp/` has no diff.

## US1 — Scheduled monitoring catches a changed scholarship

| Step | Evidence | Result |
|---|---|---|
| 1–2 Seed a source/scholarship; fixture with one new + one changed listing | `tests/workflows/test_source_monitor.py` fixtures | PASS |
| 3 Manual `POST /monitoring/runs` | `tests/api/test_monitoring_api.py` (202 with run body; 409 while a run is in progress) | PASS |
| 4 Run completed, `changes_detected`, `newly_discovered` / `updated`, prior value kept in the trail | `test_source_monitor.py`, `tests/integration/test_monitoring_alert_flow.py::test_full_catch_it_and_tell_me_loop` | PASS |
| 5 Unchanged re-run: `changes_detected=0`, `last_checked_at` advances, no new rows | `test_source_monitor.py::test_unchanged_listing_makes_no_ingestion_call_and_no_spurious_rows`, `test_source_monitor_rematch.py::test_unchanged_rerun_rematches_and_alerts_nobody` | PASS |

## US2 — Re-match and alert the affected owner

| Step | Evidence | Result |
|---|---|---|
| 1–2 Eligible owner gets one alert; `channels_requested` has both channels | `tests/services/test_alerts.py::test_new_scholarship_creates_one_alert_for_each_eligible_owner`, `test_channels_requested_snapshots_current_preferences`, integration test above | PASS |
| 3 `email_enabled=false` → `["in_app"]` only, no email send attempted | `test_alerts.py::test_both_channels_off_means_no_alert_and_no_send_but_rematch_still_runs`, `tests/api/test_alerts_api.py::test_preferences_default_when_no_row_then_partial_put_persists` | PASS |
| 4 Non-impacting change → zero new `alerts` rows | `test_alerts.py::test_non_impacting_change_creates_no_alert_rows` | PASS |

## US3 — Source health, not silent failure

| Step | Evidence | Result |
|---|---|---|
| 1–3 Failing fetch shows `failing`/non-zero `consecutive_failures`, `last_success_at` unchanged; healthy source shows `ok` with `items_found`; zero-candidate extraction recorded as `fail` | `tests/api/test_health.py`, `tests/workflows/test_source_monitor.py` (e.g. `test_a_success_between_failures_prevents_the_failing_flip`) | PASS |

## US4 — Coverage analytics

| Step | Evidence | Result |
|---|---|---|
| 1–2 Counts match the seed exactly; `claims_complete_coverage` is `false`; gap strings | `tests/api/test_coverage.py` (`test_counts_match_the_seeded_registry_and_fetch_log_exactly`, `test_response_has_the_contract_shape_and_never_claims_complete_coverage`, `test_pending_candidates_are_counted_separately_from_sources`) | PASS |

## US5 — Lifecycle state at a glance

| Step | Evidence | Result |
|---|---|---|
| Walk `newly_discovered → verified → updated → stale → source_unavailable → closed → reopened`, exactly one state at each step | `tests/integration/test_lifecycle_walk.py` (`test_u5_test_walk`, `test_fetch_failure_never_closes_or_expires_at_any_step`, `test_source_unavailable_recovers_to_verified_when_confirmed`) | PASS |

## US6 — Monitoring and discovery fetch the right page

| Step | Evidence | Result |
|---|---|---|
| 1–4 Configured listing URL is fetched; cleared → homepage + `used_homepage_fallback=true`; changed → new URL on next run | `tests/integration/test_listing_page_fetch_flow.py::test_listing_url_clear_and_change_flow`, `tests/sources/test_listing_url_resolution.py` | PASS (mocked) |
| Real-network confirmation for DAAD / Stipendium Hungaricum / KAUST | tasks.md T169 names the weekly `pytest -m smoke` run as the check; `test_discovery_listing_extraction_live.py` currently has one test (`..._ca`) and was not verified to cover all three | **NOT RUN** — open item 1 |

## US7 — Candidate-source pre-checks

| Step | Evidence | Result |
|---|---|---|
| 1–2 Candidate surfaced; validations row populated | `tests/workflows/test_source_monitor.py` (validation step), `tests/api/test_candidate_validations_api.py::test_history_is_newest_first_and_carries_every_contract_field` | PASS |
| 3 No `source_registry` row until a human approves | `test_all_pass_candidate_has_no_registry_row_until_a_human_approves` | PASS |
| 4 A candidate that failed a check can still be approved (001 endpoint) | `test_candidate_failing_a_check_can_still_be_approved` | PASS |

## US8 — Production hardening

| # | Step | What was done | Result |
|---|---|---|---|
| 1 | HTTPS: `http://<host>/health` upgrades to HTTPS | **Statically verified only**: `Caddyfile` has `tls internal`, an HTTP→HTTPS redirect and a loopback `reverse_proxy`; `tests/infra/test_deploy_config.py`. The stack was never started when this log was first written. | **DEMONSTRATED 2026-10-19** from outside the VM — see open item 3 (closed) and `docs/operations-hardening.md` §13 |
| 2 | Rate limit: `429` after threshold; second IP unaffected | `tests/core/test_rate_limit.py` (`test_request_beyond_the_limit_gets_429_with_json_body_and_retry_after`, `test_a_second_client_ip_is_unaffected`, `test_the_bucket_refills_with_time`) | PASS (in-process; behind-Caddy IP handling relies on `--proxy-headers`, asserted statically, not exercised live) |
| 3 | Retry/backoff: fail twice then succeed → one row, `retry_count=2`, `ok` | `tests/sources/test_connectors.py::test_official_fetch_fail_twice_then_succeed_logs_one_ok_row_with_retry_count_2` and the exhausted-retries test beside it (T223's tests live in `test_connectors.py`, not `test_retry_policy.py` as the task text says), plus `tests/sources/test_retry_policy.py` | PASS |
| 4 | Logs/metrics: JSON log line for a run; `/metrics` counter increments | `tests/core/test_logging.py`, `tests/core/test_metrics.py`, `tests/workflows/test_source_monitor.py::test_a_run_increments_the_run_duration_and_per_status_fetch_counters` | PASS |
| 5 | Document persistence across a container restart | Static only: compose mounts the named volume `backend_storage` at `/data/storage` and forces `STORAGE_ROOT` to it (`test_deploy_config.py`); storage-failure handling is tested (`test_upload_document_returns_retryable_error_when_storage_fails`). A real restart was **not** performed. | **NOT DEMONSTRATED** (needs the VM; runbook section 10) |
| 6 | Retention: floors keep health history; old rows beyond them pruned; CLI dry run | `tests/services/test_retention.py` (floors a/b/c, scope isolation, dry run == real run, batching, CLI). Dry run against the dev DB: 0 rows. | PASS (automated); production-scale first run still to be done by the owner (runbook section 7) |
| 7 | Backups: Neon PITR enabled | Document-only check; no access to the Neon console. | **NOT DEMONSTRATED** — open item 2 |
| 8 | Security: `pip-audit` clean; no secret literals; sanitization | `pip-audit` run (clean, see above); `tests/test_no_secrets_in_repo.py` (also scans `Caddyfile`, compose, `.env.example`); `tests/sources/test_monitor_sanitization.py` | PASS |

## The three honest open items

These could not be demonstrated by this work and are the owner's to close. Nothing else in this
log is outstanding for them.

1. **Live-network smoke check.** The 5 `-m smoke` tests (`tests/smoke/test_live_services.py`,
   `tests/smoke/test_discovery_listing_extraction_live.py`) hit real Qdrant/Gemini and real
   scholarship sites, are deselected by default and run weekly in `.github/workflows/deep-ci.yml`.
   They were **not run** here. Command: `cd backend && uv run pytest -m smoke`. T169 designates it as the real-network check for the three re-enabled
   listing sources (US6); whether its single listing test covers all three was not verified.
2. **Neon point-in-time recovery.** Whether PITR is enabled, its restore window, and a restore
   drill into a scratch branch have not been checked or performed (no console access). Steps:
   `docs/operations-hardening.md` section 8. Record the date, restore point and result here.
3. **HTTPS from the public internet. — CLOSED 2026-10-19.** Deployed to the Oracle VM
   (`002-source-monitoring-hardening` @ `e7b3c24`, 2026-10-18) and verified from outside the VM:
   `https://130.61.159.24/health` returns 200 (self-signed `tls internal` certificate);
   `http://130.61.159.24/` redirects (308) to HTTPS; `https://…/metrics` returns 404 (also for
   `/Metrics`); plain `http://…:8000` is unreachable from outside and `8000/tcp` was removed from
   firewalld. The first external attempt failed because the Oracle security-list ingress rules for
   80/443 were missing (a console step); it passed once they were added. Full record, rollback
   info and the security-list pitfalls: `docs/operations-hardening.md` §13 and §3.

   *Items 1 and 2 above remain open and are unchanged by this update.*

## Additional unverified items found while writing this log

Not among the three, but equally unproven:

- **The `Caddyfile` has not been validated by Caddy.** — *Resolved 2026-10-18:* `caddy validate`
  (Caddy 2, via podman on the VM) reported `Valid configuration` before the deploy.
- **The compose file has not been brought up** under podman-compose on Oracle Linux. — *Resolved
  2026-10-18:* brought up with rootful podman-compose; SELinux `:z` mount, the `${SITE_ADDRESS:-…}`
  default and the loopback bind all worked (no AVC denials). The container names turned out to be
  `scholarship-ai-assistant_backend_1` / `_caddy_1`.
- **Document persistence across a real redeploy** (US8 step 5) — see row 5 above.
- **The first production retention run** (large one-time delete) — see row 6 above.

## Drift and gaps found and fixed during B4

| Finding | Resolution |
|---|---|
| `Alert.channels_requested` was documented in the contract as `enum: [in_app, email]`; the live schema is intentionally `list[str]` so a future channel needs no schema change (`app/schemas/alert.py` says so) | **Contract was wrong → fixed** (open string items with a description) |
| `AlertPreferencesUpdate` fields were non-nullable in the contract; the live model accepts `null`, meaning "leave unchanged" (`alert_repo.upsert_preferences`) | **Contract was wrong → fixed** (`nullable: true`, description added) |
| `POST /alerts/{id}/read` vs the live path param `{alert_id}` | Cosmetic (parameter names are not wire-visible). The test normalises path-parameter names; neither side changed |
| Contract marks some defaulted/nullable fields `required` (e.g. `SourceHealth.last_checked_at`); Pydantic leaves them out of `required` | Wire-equivalent (always serialised). Encoded as a rule in the test, guarded by a check that no route uses `response_model_exclude*` |
| `CoverageSnapshot.claims_complete_coverage`: contract `enum: [false]`, live `const: false` | Same meaning; the comparator folds `const` into `enum` |
| T171 guardrail test file `test_source_monitor_guardrails.py` was never created in US1 though the invariant held | **Created** in B4 (4 tests) |
| ~78 task boxes (T143–T215, T249, T250) unticked in `tasks.md` although committed | Ticked after checking each against its files and commit; T171/T172/T173 annotated with actual file names |
