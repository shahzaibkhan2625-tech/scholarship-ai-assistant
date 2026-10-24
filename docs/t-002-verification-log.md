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
| 5 | Document persistence across a container restart | Static checks (compose volume at `/data/storage`, `STORAGE_ROOT` forced) **and a real recreate on the VM, 2026-10-22**: uploaded a PDF through the live HTTPS API, removed and recreated both containers (new backend ID, same image, no `--build`), then compared SHA-256 and size of the file in the volume, the `application_documents.checksum`, and a read through the app's storage layer. All identical. Test data removed afterwards. The upload itself returned a 500 (Gemini quota; see defect below) after the file and row were saved. | **PASS 2026-10-22** (procedure: `docs/operations-hardening.md` section 10) |
| 6 | Retention: floors keep health history; old rows beyond them pruned; CLI dry run | `tests/services/test_retention.py` (floors a/b/c, scope isolation, dry run == real run, batching, CLI). Dry run against the dev DB: 0 rows. **First production run 2026-10-22 on the VM: dry run 0 rows, real run `deleted 0 row(s)`**; the fetch log held 650 rows, oldest 44 days (inside the 90-day window), so 0 is correct. | PASS (automated); **first production run PASS 2026-10-22** (nothing old enough to delete yet; first real deletion expected ~2026-12-07) |
| 7 | Backups: Neon PITR enabled | Restore drill performed 2026-10-24 (branch from a point in time, row counts compared, branch deleted) — see open item 2, now closed. | **PASS 2026-10-24** (open item 2, closed) |
| 8 | Security: `pip-audit` clean; no secret literals; sanitization | `pip-audit` run (clean, see above); `tests/test_no_secrets_in_repo.py` (also scans `Caddyfile`, compose, `.env.example`); `tests/sources/test_monitor_sanitization.py` | PASS |

## The three honest open items

These could not be demonstrated by this work and are the owner's to close. Nothing else in this
log is outstanding for them.

1. **Live-network smoke check.** The 5 `-m smoke` tests (`tests/smoke/test_live_services.py`,
   `tests/smoke/test_discovery_listing_extraction_live.py`) hit real Qdrant/Gemini and real
   scholarship sites, are deselected by default and run weekly in `.github/workflows/deep-ci.yml`.
   They were **not run** here. Command: `cd backend && uv run pytest -m smoke`. T169 designates it as the real-network check for the three re-enabled
   listing sources (US6); whether its single listing test covers all three was not verified.
   **Update 2026-10-22 (partial run):** only the two Qdrant tests were run (`-k qdrant`; the Gemini quota was
   exhausted, so the Gemini and discovery tests were deliberately deferred). **Both failed**: `ConnectError: [WinError 10054]
   connection forcibly closed`. The same TLS reset was reproduced with plain `curl` from the VM, so it is not this laptop's
   network. Most likely the free-tier Qdrant cluster is suspended or removed (the failure mode T130 found); not confirmed
   without the Qdrant console. **Still open**, and now a real finding: the vector search behind Q&A and matching may be down.
   **Update 2026-10-24 (Gemini quota reset, after the Neon password rotation):** `test_gemini_generation_reachable`
   **PASSED**. `test_discovery_endpoint_extracts_real_listing_candidates_across_two_source_types` **FAILED again**, but
   not with a 429/quota error — confirmed via debug-level logs, no rate-limit text anywhere. It is a genuine assertion
   failure: the discovery endpoint returned 0 candidates from the two freshly-seeded dedicated sources, expected ≥2.
   Not retried. Qdrant was not re-tested this time. **Still open**, now narrowed to a real discovery-endpoint defect
   rather than quota exhaustion.
2. **Neon point-in-time recovery. — CLOSED 2026-10-24.** PITR restore drill performed (procedure:
   `docs/operations-hardening.md` §8): created a scratch branch from a point in time ~10:00 AM PKT on 2026-10-24,
   compared against production, then deleted the branch. `scholarships` 34 = 34 (match); `source_fetch_log` 798
   (branch) vs 818 (production) and `monitoring_runs` 129 (branch) vs 134 (production) — both lower on the branch,
   as expected for an earlier point in time; Alembic head matched on both. All comparisons were read-only against
   the branch's own connection string, which was never written to the VM's or the laptop's `.env`. This proves a
   restorable window of **at least ~5 hours** on the Neon Free plan; the plan's exact retention limit was not
   otherwise confirmed from the console.
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
- ~~Document persistence across a real redeploy~~ - **resolved 2026-10-22** (PASS, row 5 above).
- ~~The first production retention run~~ - **resolved 2026-10-22** (0 rows, correct; row 6 above). The first run that actually deletes rows is still ahead.

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

## Post-deployment verification, 2026-10-22

| Item | Result |
|---|---|
| Document persistence across a real container recreate | **PASS** (row 5) |
| First production retention run | **PASS**, 0 rows deleted, correct (row 6) |
| Live smoke tests | **Partial / FAIL**: Qdrant pair failed (connection reset); Gemini and discovery tests not run (daily quota exhausted), to be run after the quota resets. **Updated 2026-10-24:** Gemini test now PASSED (quota reset); discovery test FAILED again, but on a real bug (a swallowed provider error produced 0 candidates), not quota — fixed to log, not yet re-verified live. See open item 1 and the 2026-10-24 section below. |
| Neon point-in-time recovery | **Still open** (open item 2). **Updated 2026-10-24: CLOSED** — restore drill passed, see open item 2 and the 2026-10-24 section below. |

Defects and observations found while doing this:
- **Upload returns a bare 500 when the LLM provider errors** (Gemini 429). The saved file and row are kept but carry no flag.
  Fixed on branch `fix/upload-llm-provider-errors` (file and row kept, row flagged `extraction_unavailable`, 201); not yet deployed.
- **Gemini free-tier quota (20 requests/day observed) is exhausted**, which also blocks the LLM smoke tests.
- **Qdrant Cloud unreachable** from both networks (see open item 1 update).
- **SSH to the VM flapped** (port 22 timed out for minutes, three times) while HTTPS stayed up; self-recovering, cause unknown.
- Removing the backend container requires removing Caddy first (`depends_on`); runbook sections 10 and 12 updated.

## Post-deployment verification, 2026-10-24

| Item | Result |
|---|---|
| Neon password rotation | Role `neondb_owner` password rotated on the Neon console (production branch, pooled endpoint). Laptop and VM `.env` updated; VM containers recreated (`scholarship-ai-assistant_caddy_1` then `scholarship-ai-assistant_backend_1`, no `--build`). Verified via loopback + HTTPS `/health` 200, scheduler restarted with no DB errors, and a SHA-256 hash match (never the value) between the running container's `DATABASE_URL` and the laptop's. No password or connection string appears here or anywhere else in the repo. |
| Neon point-in-time recovery | **PASS** — see open item 2 above (now closed) for the full drill record |
| Live smoke test — Gemini | **PASS** (`test_gemini_generation_reachable`, quota had reset) |
| Live smoke test — discovery | **FAIL** — not quota; a genuine assertion failure (0 candidates, expected ≥2). See open item 1 update above. |
