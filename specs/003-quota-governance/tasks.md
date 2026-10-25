# Tasks: LLM Quota Governance & Cheap Monitoring

**Input**: `specs/003-quota-governance/` — plan.md, spec.md, research.md, data-model.md, contracts/api-delta.md; `adr/0014-llm-budget-gate-and-fetch-outcomes.md`
**Prerequisites**: plan.md, spec.md (both present and approved). Continues the T-id sequence from `specs/002-source-monitoring-hardening/tasks.md` (last id T250) — **first id here is T251**.
**Tests**: MANDATORY (constitution Principle V; plan.md Technical Context). Every implementation task is preceded by a test task that MUST be written first and MUST fail before the implementation task starts. No test makes a live provider call; the provider is always a fake, and the existing `-m smoke` tests stay deselected.
**Migrations**: Tasks below only *generate* the Alembic revision file. **No task runs `alembic upgrade`** — you apply the migration yourself. DB-backed tests will fail until you have applied it; that is expected and called out where it matters.

## Format: `- [ ] T### [P?] [US?] Description — path (NEW|EXTENDED) — Depends: … — Satisfies: …`

- **[P]**: different file, no dependency on an incomplete task.
- **[USn]**: user-story phases only (Setup, Foundational and Polish carry no story label).
- **NEW / EXTENDED**: whether the file is created by this feature or gains additive code in an existing file (existing public signatures keep working).
- All paths are relative to the repo root `scholarship-ai-assistant/`.
- Steps marked **[OWNER]** are performed or approved by you; Claude stops and waits there.

## Hard rules baked into every task (from plan.md, ADR-0012/0013/0014 and the constitution)

- `core/llm.py` is the **only** place that talks to the provider, and every call passes the gate. `core` imports no database code.
- Monitoring stays a **deterministic workflow, never an agent**; hard-constraint matching is untouched (Principle II). When the budget refuses a soft-preference call, a student gets the 503 and an alert re-match pair is skipped; no verdict is produced with a guessed soft result.
- A provider-side failure **never** changes source health, closes or empties a record, or reads as "no scholarships found" (Principle IV, FR-ERR-2/3).
- Extraction rules are **human-defined configuration**; the LLM never authors or activates them (FR-RULES-5). Rule results pass the same grounding gate (threshold 1.0) as LLM results.
- **No HEC category is excluded.** HEC rules are enabled only after the parity check passes and you approve the report (plan decision 7).
- Production is **never re-seeded** for this feature (the seed loader would reset EACEA `failing` to `active` and replace `extraction_rules`); a dedicated set-rules command is used instead.
- No secret, credential, page content or `str(exception)` of a provider error is written to a log, a metric label or a recorded reason (FR-ERR-4).
- Nothing under `specs/001-*` or the 002 spec/contract files is modified; the live-drift contract for 003 is a new file (T276).

## Phase order (differs slightly from plan.md's suggested slices)

Plan.md listed the slices as US2, US4, US1, US3, US5, US6. Here the order is **US2, US1, US4, US3, US6, US5** so that both P1 stories finish first (MVP), the P2 stories follow, and US5 (P3, which depends on cadence, the connector changes and an owner-approved LLM snapshot) comes last. All slices touching `official_fetch.py` are sequential (T284, T290, T316), so there are no edit conflicts.

---

## Phase 1: Setup

**Purpose**: configuration surface.

- [ ] T251 [P] Write failing tests that `Settings()` loads with none of the new variables set and exposes the documented defaults: generation allowance 20, embedding allowance unset (counted, not limited), user-facing reserve 8, ladder offsets backfill 0 / monitoring 2 / validation 4, backfill daily share 6, budget timezone `America/Los_Angeles`, default provider pause 900 s, cadence minutes weekly 1440 / monthly 10080 / quarterly 10080 / annual 10080 / undeclared 1440, forced full re-check 7 days, unextractable back-off base 60 min, noise window 10 checks and threshold 0.8, rules anomaly ratios 0.5 and 2.0, ledger retention 90 days; and that invalid values are rejected (reserve not below allowance, negative offsets, unknown timezone) — `backend/tests/core/test_config_quota.py` (NEW) — Satisfies: FR-BUDGET-2/11, FR-CADENCE-2
- [ ] T252 Add those settings (all optional, safe defaults, validators for the rejection cases; no existing setting changes) — `backend/app/core/config.py` (EXTENDED) — Depends: T251 — Satisfies: FR-BUDGET-2/11

---

## Phase 2: Foundational (blocks every user story)

**Purpose**: schema, models, migration file, the gate interface, repositories and the fetch-log `outcome` plumbing. Nothing in Phases 3+ may start before this phase is complete.

### Tests first (must fail before the implementations below)

- [ ] T253 [P] Write failing static tests (no DB) that `Base.metadata` contains: `llm_budget_ledger` (PK on budget_day, request_type, priority_class, event; `count` int not null default 0), `llm_budget_state` (PK request_type; `paused_until`, `pause_reason`, `pause_set_at`, `observed_limit`), `source_check_state` (PK/FK `source_id` to `source_registry.id`; fingerprint, fingerprint_at, etag, last_modified, validators_trusted, last_extraction_outcome, last_full_extraction_at, backoff_level, next_attempt_at, checks_window, changed_window, rules_review_needed, rules_review_reason, updated_at), `source_fetch_log.outcome` (varchar 24, nullable), and `monitoring_runs.sources_skipped` / `sources_deferred` (int, not null, server default 0) — `backend/tests/data/test_quota_models.py` (NEW) — Satisfies: data-model.md
- [ ] T254 [P] Write failing static tests (no DB connection) that parse the new revision file and assert: it exists; its `down_revision` is the current head `f2b8d4a6c1e3` (confirm with the read-only `alembic heads` before writing the test); `upgrade()` only creates the three tables and adds the three columns (no drop, no alter of an existing column, no CHECK on `status`); `downgrade()` removes exactly those objects; there is a single head — `backend/tests/infra/test_quota_migration.py` (NEW) — Satisfies: plan.md Migration statement
- [ ] T255 [P] Write failing tests for the gate interface: `Priority` has exactly `user_facing`, `backfill`, `monitoring`, `validation`; `RequestType` has `generate`, `embed`; `llm_priority(...)` scopes nest and restore on exit and on exception; a call with no priority raises `LlmPriorityMissing`; with no gate installed a call is refused (fail closed); exception hierarchy has base `LlmError` with `LlmBudgetDeferred(retry_at)`, `LlmTemporarilyUnavailable(retry_at, reason in {daily_allowance_spent, provider_quota, provider_unavailable})`, `LlmQuotaExhausted`, `LlmProviderError`, `LlmResponseUnparseable`; `core/llm_gate.py` imports nothing from `app.data`, `app.models` or `app.services` (AST check) — `backend/tests/core/test_llm_gate.py` (NEW) — Satisfies: FR-BUDGET-1/3, R-01
- [ ] T256 [P] Write failing repository tests using the per-test rollback fixture: ledger upsert increments atomically (`count = count + 1`), per-class and per-event sums, "allowed today" total per request type, pause set/get/clear, prune rows older than N days; check-state get/create/update and bootstrap (a source with N consecutive "listing extraction yielded 0 candidates" fetch-log rows gets a matching `backoff_level`; a source with none starts at 0) — `backend/tests/data/test_llm_budget_repo.py` and `backend/tests/data/test_check_state_repo.py` (NEW) — Satisfies: FR-BUDGET-8, R-04, R-08
- [ ] T257 [P] Write failing tests that `SourceFetchLogCreate` accepts `outcome` from the allowed set (`unchanged`, `deferred`, `rules`, `provider_quota`, `provider_error`, `provider_parse`, or None) and rejects anything else, and that `source_repo.log_fetch` persists it and defaults it to None when omitted — `backend/tests/data/test_source_repo_outcome.py` (NEW) — Satisfies: R-10

### Implementations

- [ ] T258 [P] Create the ORM models `LlmBudgetLedger`, `LlmBudgetState` and `SourceCheckState`, and register both new modules in Alembic's model imports — `backend/app/models/llm_budget.py` (NEW), `backend/app/models/check_state.py` (NEW), `backend/migrations/env.py` (EXTENDED) — Depends: T253 — Satisfies: data-model.md
- [ ] T259 [P] Add `SourceFetchLog.outcome` (varchar 24, nullable) and `MonitoringRun.sources_skipped` / `sources_deferred` (int, not null, default 0) — `backend/app/models/source.py` (EXTENDED), `backend/app/models/monitoring.py` (EXTENDED) — Depends: T253 — Satisfies: R-10, FR-OBS-3
- [ ] T260 Generate (do **not** run) the single additive Alembic revision with `down_revision = f2b8d4a6c1e3`: create the three tables, add the three columns, matching `downgrade()`; add a header comment stating "additive only, apply before deploying 003 code, safe to leave applied on rollback" — `backend/migrations/versions/<rev>_quota_governance.py` (NEW) — Depends: T254, T258, T259 — Satisfies: plan.md Migration statement
- [ ] T261 [P] Implement the gate interface: `Priority`, `RequestType`, the context variable and `llm_priority` context manager, the exception hierarchy, and the gate registry (`install_gate`, `get_gate`, `LlmGate` protocol with `acquire` and `report_provider_error`) — no database imports — `backend/app/core/llm_gate.py` (NEW) — Depends: T255 — Satisfies: FR-BUDGET-1/3
- [ ] T262 [P] Implement the repositories (SQL only here): ledger increment/sums/prune, pause get/set/clear, advisory-lock helper keyed by (budget day, request type), check-state CRUD and the history bootstrap — `backend/app/data/repositories/llm_budget_repo.py` (NEW), `backend/app/data/repositories/check_state_repo.py` (NEW) — Depends: T256, T258 — Satisfies: FR-BUDGET-8, R-04, R-08
- [ ] T263 Extend `SourceFetchLogCreate` with the typed `outcome` field and pass it through `log_fetch` — `backend/app/schemas/source.py` (EXTENDED), `backend/app/data/repositories/source_repo.py` (EXTENDED) — Depends: T257, T259 — Satisfies: R-10

**Checkpoint**: Foundational complete. **[OWNER] Apply the migration (T260) before running the DB-backed tests in later phases.**

---

## Phase 3: User Story 2 — A central daily LLM budget protects user-facing work (P1)

**Goal**: every provider call is counted and classed; background work yields to students; provider quota signals pause calls.
**Independent test**: with a fake provider and a small allowance, background work stops at the ladder floors, the reserve is still available to students, a simulated quota error pauses non-user-facing work until the stated time, and a refused student request gets a 503 with `Retry-After` and no provider call.

### Tests first

- [ ] T264 [P] [US2] Write failing tests for the pure admission policy `decide(...)`: user-facing admitted down to 0 remaining; backfill needs remaining above the reserve (and is capped at 6 a day); monitoring needs remaining above reserve + 2; validation above reserve + 4; refused background gives `deferred`, refused student gives `denied`; an active pause blocks all non-user-facing and also user-facing provider calls; `retry_at` is the next boundary or the pause end; budget-day boundary math at midnight `America/Los_Angeles` with a fake clock including a daylight-saving change — `backend/tests/services/test_llm_budget_policy.py` (NEW) — Satisfies: FR-BUDGET-2/4/5/10, R-02, R-05
- [ ] T265 [P] [US2] Write failing tests for error classification using the real 2026-10-24 daily-quota error shape saved as a fixture (code 429, status RESOURCE_EXHAUSTED, `RetryInfo` 340 s, `QuotaFailure` with a `...PerDay...` quota id): pause until the next budget-day boundary, not 340 s; a per-minute violation pauses for the provider's `retryDelay` (minimum 60 s); no hint pauses 900 s; 5xx and network errors become `LlmProviderError`; the error text is never copied into the exception message — `backend/tests/services/test_llm_error_classification.py` (NEW), `backend/tests/fixtures/llm/quota_error_daily.json` (NEW) — Satisfies: FR-BUDGET-6/7, R-03
- [ ] T266 [P] [US2] Write failing DB-backed gate tests (inject the rollback session factory so the gate's own short transaction does not write to the shared database): two concurrent acquisitions never exceed the allowance; counts are kept per class and event; deferred and denied events are recorded; a provider 429 sets the pause and records a `discrepancy` when the local count was below the allowance; a new gate instance sees the same counts (restart-safe); if the budget store raises, background is deferred and a student request proceeds and is counted — `backend/tests/services/test_llm_budget_gate.py` (NEW) — Satisfies: FR-BUDGET-7/8/9, R-04
- [ ] T267 [P] [US2] Write failing tests for the provider wrapper with a fake gate and fake client: `acquire` happens before the client call; a refusal makes no client call; exactly one client call per operation (fails if client-level retries are ever enabled); `APIError` is translated and reported; embeddings count under `embed`; `LlmNotConfiguredError` behaviour is preserved — `backend/tests/core/test_llm_wrapper.py` (NEW) — Satisfies: FR-BUDGET-1/6, R-03
- [ ] T268 [P] [US2] Write failing API tests, parametrised over the eight student routes in `contracts/api-delta.md` section 1: a refusing gate yields `503`, a `Retry-After` header, and exactly the documented JSON body (`code`, `retry_after_seconds`, `retry_at`, `reason`) with no provider call and no secrets or stack trace; the document-upload route still returns 201 with `extraction_unavailable` when an `LlmError` occurs — `backend/tests/api/test_llm_unavailable.py` (NEW) — Satisfies: FR-BUDGET-5, plan decision 6
- [ ] T269 [P] [US2] Write failing tests that priority scopes are set at the entry points: an HTTP request runs as `user_facing`; `run_source_monitor` and the alert re-match step run as `monitoring`; the candidate-validation step runs as `validation`; a backfill helper runs as `backfill`; a fake gate records the class seen at each — `backend/tests/workflows/test_llm_priority_scopes.py` (NEW) — Satisfies: FR-BUDGET-3
- [ ] T270 [P] [US2] Write a failing guard test that enumerates every module under `backend/app` that calls `generate_text` or `embed_texts` (AST scan) and fails if one is not in a documented allowlist mapping it to its entry-point class, so a new LLM-backed path cannot be added unclassified — `backend/tests/core/test_llm_entry_points.py` (NEW) — Satisfies: FR-BUDGET-1/3

### Implementations

- [ ] T271 [US2] Implement the database-backed gate: pure `decide(...)` policy, budget-day boundary, atomic reservation under the advisory transaction lock on its own short session, ledger events, provider-error classification and pause, fail-closed/fail-safe behaviour, injectable session factory and clock — `backend/app/services/llm_budget.py` (NEW) — Depends: T261, T262, T264, T265, T266 — Satisfies: FR-BUDGET-2..9, R-02..R-05
- [ ] T272 [US2] Route both provider functions through the gate: acquire before the call, translate `APIError` after it, report quota pauses; keep `LlmNotConfiguredError` and the existing signatures — `backend/app/core/llm.py` (EXTENDED) — Depends: T267, T271 — Satisfies: FR-BUDGET-1/6
- [ ] T273 [US2] Install the gate in the application lifespan, add the middleware that sets `user_facing`, and add the exception handler that maps `LlmTemporarilyUnavailable` to the documented 503 body with `Retry-After` — `backend/app/main.py` (EXTENDED) — Depends: T268, T271 — Satisfies: FR-BUDGET-5, plan decision 6
- [ ] T274 [US2] Declare the other classes at their entry points with `llm_priority`: `monitoring` around `run_source_monitor` and the alert re-match step, `validation` around the candidate-validation step, and a `run_as_backfill` helper for later features — `backend/app/workflows/source_monitor/graph.py` (EXTENDED), `backend/app/core/llm_gate.py` (EXTENDED) — Depends: T269, T270, T271 — Satisfies: FR-BUDGET-3
- [ ] T275 [US2] Make the document pipeline catch `LlmError` (and `LlmNotConfiguredError`) so the upload fix keeps returning 201 with `extraction_unavailable` — `backend/app/workflows/doc_pipeline/graph.py` (EXTENDED, handler near line 143) — Depends: T268, T272 — Satisfies: FR-BUDGET-5
- [ ] T276 [US2] Create the 003 live-drift contract: copy the 002 `openapi.yaml` to `specs/003-quota-governance/contracts/openapi.yaml`, add the `503` response to the eight student routes, and point `CONTRACT_PATH` in the existing drift test at it (the 002 yaml stays as the historical record) — `specs/003-quota-governance/contracts/openapi.yaml` (NEW), `backend/tests/api/test_openapi_contract_002.py` (EXTENDED) — Depends: T273 — Satisfies: contracts/api-delta.md section 1

**Checkpoint**: US2 complete. The gate exists and refuses; monitoring still spends the same amount until US1.

---

## Phase 4: User Story 1 — Unchanged pages cost no LLM call (P1) 🎯 MVP with US2

**Goal**: a page whose visible text is unchanged makes zero LLM requests and is recorded as a successful "unchanged" check.
**Independent test**: two consecutive monitoring checks against an unchanged fixture make zero provider requests on the second; a real change makes exactly one.

### Tests first

- [ ] T277 [P] [US1] Write failing tests for the fingerprint: two trimmed copies of the real HEC page that differ only in generated element ids, script hashes and whitespace give the same SHA-256; adding one card changes it; the function is deterministic and normalises through the existing `clean_html_to_text`; noise-window helpers flag a source noisy at 8 changes in 10 checks — `backend/tests/services/test_content_fingerprint.py` (NEW), `backend/tests/fixtures/hec/listing_a.html` and `listing_b_noise.html` (NEW, trimmed from the real page: the card container plus head/script noise) — Satisfies: FR-SKIP-1/7, R-06
- [ ] T278 [P] [US1] Write failing connector tests with a fake fetch and a counting fake extractor: a second identical check does not call the extractor, returns status `unchanged`, writes an `outcome='unchanged'` row with `items_found` NULL and skips candidate-link detection; a changed page calls the extractor once and stores the new fingerprint only after success; first-ever check extracts; an unchanged page whose last extraction failed is retried with doubling back-off capped at the cadence interval; a forced full re-check happens after `min(7 days, freshness window / 2)`; a deferred extraction leaves the fingerprint untouched; with no state row, back-off is bootstrapped from history (136 zero-candidate rows give the cap) — `backend/tests/sources/test_official_fetch_fingerprint.py` (NEW) — Satisfies: FR-SKIP-2/4/5/6, R-07, R-08
- [ ] T279 [P] [US1] Write failing monitor tests: an `unchanged` result touches the check as a success and applies the healthy transition but runs no change detection, no `mark_seen_verified` and no stale sweep (assert that records and lifecycle states are untouched even when the listed-names set is empty), increments the skipped counter and records an `avoided_unchanged` ledger event; two consecutive runs on an unchanged fixture make zero provider requests on the second — `backend/tests/workflows/test_source_monitor_unchanged.py` (NEW) — Satisfies: FR-SKIP-2/6, R-07
- [ ] T280 [P] [US1] Write failing tests for conditional requests: optional request headers pass through `fetch_url`; a 304 returns a result with `not_modified=True` and no body; a server that ignores `If-Modified-Since` and returns 200 (as HEC does) is handled; validators are sent only when trusted (a stable ETag, or a `Last-Modified` constant across at least two checks with an equal fingerprint); an injected fake `fetch(url)` with the old one-argument signature still works — `backend/tests/tools/test_web_fetch_conditional.py` (NEW) — Satisfies: FR-SKIP-3, R-06
- [ ] T281 [P] [US1] Write failing tests that `listing_fetch_tool` maps `unchanged` to the existing `cached` outcome with the persisted scholarship refs, and that the discovery agent handles the new `unchanged` and `deferred` statuses without error or a false "no results" — `backend/tests/tools/test_listing_fetch_unchanged.py` (NEW), `backend/tests/agents/test_discovery_agent.py` (EXTENDED) — Satisfies: R-07

### Implementations

- [ ] T282 [US1] Implement the fingerprint service (normalise through `clean_html_to_text`, SHA-256, noise-window update and noisy flag) — `backend/app/services/content_fingerprint.py` (NEW) — Depends: T277, T262 — Satisfies: FR-SKIP-1/7
- [ ] T283 [US1] Add optional request headers and 304 handling to `fetch_url`, and `not_modified`, `etag`, `last_modified` to `FetchResult`; keep the one-argument call working — `backend/app/tools/web_fetch.py` (EXTENDED) — Depends: T280 — Satisfies: FR-SKIP-3
- [ ] T284 [US1] In `fetch_and_extract_listing`, after the redirect checks and before candidate-link detection, add the fingerprint decision (skip, forced re-check, back-off with history bootstrap, trusted conditional headers), the new `unchanged` result status, state read/write, the `outcome='unchanged'` row and the `avoided_unchanged` ledger event; store the new fingerprint only after a successful extraction — `backend/app/sources/connectors/official_fetch.py` (EXTENDED) — Depends: T278, T282, T283, T262 — Satisfies: FR-SKIP-2/4/5/6, R-07, R-08
- [ ] T285 [US1] In `_monitor_one_source`, add the `unchanged` branch (success touch and healthy transition only; no detection, no `mark_seen_verified`, no sweep) and the skipped counter on `_SourceStats` — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T279, T284 — Satisfies: FR-SKIP-2/6
- [ ] T286 [US1] Map `unchanged` to `cached` in `listing_fetch_tool` and handle `unchanged` / `deferred` in the discovery step — `backend/app/tools/listing_fetch.py` (EXTENDED), `backend/app/agents/discovery/agent.py` (EXTENDED) — Depends: T281, T284 — Satisfies: R-07

**Checkpoint**: US1 + US2 = MVP. On a quiet day monitoring makes no provider request for unchanged pages.

---

## Phase 5: User Story 4 — Provider failures are recorded, never silent (P2)

**Goal**: provider-side failures are recorded with a distinct outcome on both monitoring and discovery paths and never move source health or records.
**Independent test**: inject a quota error, a provider outage and an unparseable response for a fixture source; three distinct recorded outcomes, health unchanged, no record changed.

### Tests first

- [ ] T287 [P] [US4] Write failing connector tests: a quota error becomes a deferred result with a `provider_quota` row (`status=fail`) and a `next_attempt_at`; an outage gives `provider_error`; an unparseable response gives `provider_parse`; a budget deferral gives an `outcome='deferred'` row with `status=ok`; none calls `mark_source_failing`, changes any scholarship, or stores the fingerprint; the same happens when the call comes through `listing_fetch_tool` (discovery path); recorded reasons are short and contain no secret, page content or provider payload — `backend/tests/sources/test_official_fetch_provider_errors.py` (NEW) — Satisfies: FR-ERR-1..4
- [ ] T288 [P] [US4] Write failing reader tests: the shared failure-streak definition (`ranked_monitoring_runs`) ignores `provider_*` rows (an interleaved provider failure does not reset or extend a streak; a real site failure afterwards still counts); the coverage failed-source count ignores them; retention floors are unchanged; `GET /sources/health` and `/sources/{id}/fetch-log` expose `outcome`, `recent_provider_failures` and `last_provider_failure_at` separately from `consecutive_failures` — `backend/tests/data/test_source_repo_streak_provider.py`, `backend/tests/services/test_coverage_provider.py`, `backend/tests/api/test_health_provider.py` (NEW) — Satisfies: FR-ERR-2/5, R-10
- [ ] T289 [P] [US4] Write failing tests: `_record_crashed_check` records an `LlmError` as a provider outcome with no health transition, while any other exception keeps today's behaviour (a `[monitor]` FAIL row plus a health failure); the candidate-validation step records the provider outcome on its advisory result and stops after the first deferral instead of looping through the remaining candidates — `backend/tests/workflows/test_source_monitor_provider_errors.py`, `backend/tests/services/test_candidate_validation_provider.py` (NEW) — Satisfies: FR-ERR-1/2, R-10

### Implementations

- [ ] T290 [US4] Wrap the extraction call in the connector: translate gate and provider exceptions into the deferred / `provider_*` results and rows above, set `next_attempt_at`, never call `mark_source_failing` for them — `backend/app/sources/connectors/official_fetch.py` (EXTENDED) — Depends: T287, T284, T272 — Satisfies: FR-ERR-1..3
- [ ] T291 [US4] Make the readers ignore `provider_*`: the streak definition, the coverage failed-source count, and confirm retention is unaffected; add the new health fields to the schema and the two health endpoints — `backend/app/data/repositories/source_repo.py` (EXTENDED), `backend/app/services/coverage.py` (EXTENDED), `backend/app/schemas/health.py` (EXTENDED), `backend/app/api/health.py` (EXTENDED) — Depends: T288, T263 — Satisfies: FR-ERR-2/5, R-10
- [ ] T292 [US4] Handle `LlmError` in `_record_crashed_check`, stop the validation loop after the first deferral, and record the provider outcome on the advisory validation result — `backend/app/workflows/source_monitor/graph.py` (EXTENDED), `backend/app/services/candidate_validation.py` (EXTENDED) — Depends: T289, T290 — Satisfies: FR-ERR-1/2
- [ ] T293 [US4] Add the health-response fields to the 003 contract and keep the drift test green — `specs/003-quota-governance/contracts/openapi.yaml` (EXTENDED) — Depends: T291, T276 — Satisfies: contracts/api-delta.md section 2

---

## Phase 6: User Story 3 — Each source is checked as often as it actually changes (P2)

**Goal**: the scheduler tick only selects due sources; annual sources are not read hourly; an idle tick logs one line and writes no run row.
**Independent test**: simulate 14 days of ticks for one annual and one weekly source and count checks; neither goes stale.

### Tests first

- [ ] T294 [P] [US3] Write failing pure tests for the cadence service: interval by update frequency (weekly 1 day; monthly, quarterly, annual 7 days; undeclared 1 day), override precedence, interval shortened or flagged when it would exceed the freshness window, `due = max(next_attempt_at, last_checked_at + interval)`, stalest-first order, back-off doubling capped at the interval, and a 14-day simulation (annual at most 3 checks, weekly at most 15, no record stale because of the schedule) — `backend/tests/services/test_cadence.py` (NEW) — Satisfies: FR-CADENCE-1..5, SC-004
- [ ] T295 [P] [US3] Write failing schema tests that `extraction_rules['check_interval_minutes']` accepts an integer of at least 15, rejects other values, and that unknown existing keys (`list_page_url`, `robots_checked`, `allowed_path_prefixes`) are preserved untouched — `backend/tests/sources/test_source_cadence_schema.py` (NEW) — Satisfies: FR-CADENCE-3
- [ ] T296 [P] [US3] Write failing run tests: only due sources are checked; stalest first; deferred sources go first next tick; an idle scheduled tick creates no `monitoring_runs` row but logs exactly one structured line with `due`, `checked`, `skipped`, `deferred` counts; a manual run always creates a row, may name sources and obeys the budget; `sources_skipped` / `sources_deferred` are persisted and returned by the monitoring API; the per-source overlap lock is preserved — `backend/tests/workflows/test_source_monitor_cadence.py` (NEW), `backend/tests/scheduling/test_scheduler_tick.py` (NEW), `backend/tests/api/test_monitoring_api.py` (EXTENDED) — Satisfies: FR-CADENCE-5/6, FR-OBS-3, plan decision 5

### Implementations

- [ ] T297 [US3] Implement the cadence service (interval derivation, due selection, stalest-first order, back-off helper) — `backend/app/services/cadence.py` (NEW) — Depends: T294, T262 — Satisfies: FR-CADENCE-1..5
- [ ] T298 [US3] Add the `check_interval_minutes` validator to the registry write schema without disturbing other keys — `backend/app/schemas/source.py` (EXTENDED) — Depends: T295 — Satisfies: FR-CADENCE-3
- [ ] T299 [US3] Make `run_source_monitor` filter to due sources in stalest-first order, count skipped and deferred, persist the new run counters, and include them in the run-finished log line — `backend/app/workflows/source_monitor/graph.py` (EXTENDED), `backend/app/data/repositories/monitoring_repo.py` (EXTENDED) — Depends: T296, T297, T285 — Satisfies: FR-CADENCE-5, FR-OBS-3
- [ ] T300 [US3] Add the cheap tick: a scheduled run first checks whether anything is due or any candidate needs validation and returns without creating a run row if not; log one structured line per tick either way; add the counters to the monitoring schemas and API — `backend/app/scheduling/scheduler.py` (EXTENDED), `backend/app/schemas/monitoring.py` (EXTENDED), `backend/app/api/monitoring.py` (EXTENDED) — Depends: T296, T299 — Satisfies: FR-CADENCE-1, FR-OBS-3, plan decision 5
- [ ] T301 [US3] Add the monitoring-run counters to the 003 contract and keep the drift test green — `specs/003-quota-governance/contracts/openapi.yaml` (EXTENDED) — Depends: T300, T276 — Satisfies: contracts/api-delta.md section 3

---

## Phase 7: User Story 6 — Budget and cadence are visible (P2)

**Goal**: the operator can read today's used / remaining / deferred / skipped numbers at any time, and every restart states the running configuration.
**Independent test**: after a mixed fixture day, the metrics match the activity and survive a restart; the startup log states interval, cadence and budget settings.

### Tests first

- [ ] T302 [P] [US6] Write failing metrics tests: the scrape-time collector reads the ledger and pause rows, so a freshly built collector (simulating a restart or second worker) shows the same values; series and labels match `contracts/api-delta.md` section 4; `source_check_noisy` reflects the state table; `/metrics` still returns 401 without a token — `backend/tests/core/test_metrics_budget.py` (NEW) — Satisfies: FR-OBS-1/4
- [ ] T303 [P] [US6] Write failing tests that `start_scheduler` logs one structured record with the tick interval, the per-frequency cadence defaults, per-source overrides (best-effort database read; a failing read does not block startup), and the allowance, reserve, ladder offsets, backfill share and budget timezone, and that no secret value appears in it — `backend/tests/scheduling/test_scheduler_startup_log.py` (NEW) — Satisfies: FR-OBS-2
- [ ] T304 [P] [US6] Write a failing scored-eval test for a new case `quota_governance` that simulates a day of mixed traffic with a fake provider and clock and asserts the invariants (never above the allowance, reserve honoured, no background request below its floor, no provider call after a quota pause, scoring 1.0) and that the runner registers it with threshold 1.0 — `backend/tests/evals/test_quota_governance_eval.py` (NEW) — Satisfies: constitution Principle V
- [ ] T305 [P] [US6] Write a failing test that the daily retention job also prunes ledger rows older than the configured retention window and leaves newer rows and the pause table alone — `backend/tests/services/test_retention_ledger.py` (NEW) — Satisfies: data-model.md

### Implementations

- [ ] T306 [US6] Add the scrape-time budget collector and the noisy-source gauge — `backend/app/core/metrics.py` (EXTENDED) — Depends: T302, T262 — Satisfies: FR-OBS-1, FR-SKIP-7
- [ ] T307 [US6] Add the startup log record in `start_scheduler` — `backend/app/scheduling/scheduler.py` (EXTENDED) — Depends: T303, T297 — Satisfies: FR-OBS-2
- [ ] T308 [US6] Add the `quota_governance` eval case and register it in the runner at threshold 1.0 — `backend/evals/cases/quota_governance.py` (NEW), `backend/evals/runner.py` (EXTENDED) — Depends: T304, T271 — Satisfies: constitution Principle V
- [ ] T309 [US6] Prune old ledger rows in the retention job — `backend/app/services/retention.py` (EXTENDED) — Depends: T305, T262 — Satisfies: data-model.md

---

## Phase 8: User Story 5 — Deterministic extraction first, the LLM only as a fallback (P3)

**Goal**: for a stable listing page the operator can configure rules that extract grounded results with no LLM request; HEC is enabled only after a parity check that you approve.
**Independent test**: with rules matching a fixture page, extraction makes zero provider requests; when the markup changes, exactly one fallback request and a review flag.

### Tests first

- [ ] T310 [P] [US5] Save the committed HEC fixture and write failing rule-engine tests: `backend/tests/fixtures/hec/listing.html` is a copy of the real listing page fetched once by plain HTTP on 2026-10-25 (container plus its 43 cards, trimmed of unrelated scripts); tests assert 43 cards, 38 unique names with order-preserving de-duplication, names read from the `data-position` attribute, detail URLs resolved and restricted to the source domain, HTML entities decoded (for example `&amp;`), grounding at 1.0 against parser-decoded text, `min_items`, anomaly flags below 50% or above 200% of the previous count, and that nothing is invoked for a source with no rules — `backend/tests/tools/test_rule_extract.py` (NEW), `backend/tests/fixtures/hec/listing.html` (NEW) — Satisfies: FR-RULES-1/2/4, R-11
- [ ] T311 [P] [US5] Write failing connector tests: rules run before any LLM call; a match gives `outcome='rules'`, zero provider requests and an `avoided_rules` ledger event; no match gives exactly one gated LLM request and sets `rules_review_needed`; an anomalous count holds the result and flags it; rule results pass the same grounding gate; provider is taken from the registry `organization` only if set and country, field, degree and funding stay unknown — `backend/tests/sources/test_official_fetch_rules.py` (NEW) — Satisfies: FR-RULES-1..4
- [ ] T312 [P] [US5] Write failing tests for the operator commands: `set` validates the rule schema and changes **only** `check_interval_minutes` and `deterministic` in `extraction_rules` (status, `listing_page_url` and every other field stay byte-identical, EACEA stays `failing`); `set` refuses to enable a rule set unless the parity check passes; `parity` prints `matched`, `LLM-only` (blocks enabling) and `rules-only` (flagged, not blocking) and passes only if every item in the recorded LLM snapshot is found by the rules (compared by normalised name); a read-only `diff` compares rule output with stored scholarships; the provider is never called (a fake gate raises if it is) — `backend/tests/services/test_source_rules_cli.py` (NEW) — Satisfies: FR-RULES-5, plan decision 7
- [ ] T313 [P] [US5] Write a failing test that the seed file carries a HEC `deterministic` rule set with `enabled: false`, that the seed schema accepts it, that no HEC category exclusion field exists, and that `tests/test_no_secrets_in_repo.py` still passes — `backend/tests/data/test_seed_rules.py` (NEW) — Satisfies: plan decision 7

### Implementations

- [ ] T314 [US5] Define the declarative rule schema (version, `item_selector`, name from attribute or selector, detail URL from selector and attribute, `provider_from_registry`, `min_items`, anomaly ratios, `enabled`) and validate it inside the registry write schema — `backend/app/schemas/extraction_rules.py` (NEW), `backend/app/schemas/source.py` (EXTENDED) — Depends: T310, T298 — Satisfies: FR-RULES-1/5
- [ ] T315 [US5] Implement the rule engine over `beautifulsoup4` (no new dependency), returning the same listing-extraction output shape — `backend/app/tools/rule_extract.py` (NEW) — Depends: T310, T314 — Satisfies: FR-RULES-1/2/4
- [ ] T316 [US5] Add the rules-first path to the connector (rules, then the gated LLM only on no match), the review flags on the check state, and the `rules` outcome — `backend/app/sources/connectors/official_fetch.py` (EXTENDED) — Depends: T311, T315, T290 — Satisfies: FR-RULES-1..4
- [ ] T317 [US5] Implement the operator commands `set`, `parity`, `diff` and `record-snapshot` (installs the gate itself; `record-snapshot` makes exactly one request under the `backfill` class and is refused without `--confirm`) — `backend/app/services/source_rules.py` (NEW) — Depends: T312, T315, T271 — Satisfies: FR-RULES-5, plan decision 7
- [ ] T318 [US5] Add the HEC rule set to the seed with `enabled: false` and document it (including the warning not to re-seed production) — `backend/app/data/seeds/seed_sources.yaml` (EXTENDED), `docs/SOURCES.md` (EXTENDED) — Depends: T313, T314 — Satisfies: plan.md R-11

### Owner-gated rollout steps (Claude stops at each)

- [ ] T319 [US5] **[OWNER]** Record the LLM snapshot for the saved HEC page: run `python -m app.services.source_rules record-snapshot --domain www.hec.gov.pk --fixture backend/tests/fixtures/hec/listing.html --confirm` once (one request from the free-tier allowance, outside the tests), and commit `backend/tests/fixtures/hec/llm_snapshot.json` (NEW) — Depends: T317, T318 — Satisfies: plan decision 7
- [ ] T320 [US5] Run `parity` against the committed fixture and snapshot, write the report (`matched`, `LLM-only`, `rules-only`) to `specs/003-quota-governance/parity-report.md` (NEW) and send it to you; **do not enable anything**. If any `LLM-only` item exists the rules are changed first and the check re-run — Depends: T319 — Satisfies: plan decision 7
- [ ] T321 [US5] **[OWNER]** After you approve the report: enable the HEC rule set in production with the `set` command (never by re-seeding), then watch one check and record the result in the runbook — `backend/app/services/source_rules.py` (run, not edited), `docs/operations-hardening.md` (EXTENDED) — Depends: T320 — Satisfies: plan decision 7, SC-010

---

## Phase 9: Polish & cross-cutting

- [ ] T322 [P] Update the runbook: the budget and pause behaviour, how to read the new metrics and the per-tick and startup log lines, the rule/cadence set command, the seed re-run hazard, `MONITORING_INTERVAL_MINUTES` guidance, and change the known-limitations row for other LLM endpoints (now a 503, no longer a bare 500) — `docs/operations-hardening.md` (EXTENDED) — Depends: T273, T300, T317
- [ ] T323 [P] Update the gap analysis rows affected by this feature (M-23, M-33, M-35, M-36 and the quota section) and keep the status counts consistent — `docs/blueprint-gap-analysis.md` (EXTENDED) — Depends: T322
- [ ] T324 Run the full backend suite, the eval score gate and `pip-audit` exactly as CI does; confirm the secrets scan passes, that no new dependency was added, and that the 003 contract drift test is green — `backend/` (verify only) — Depends: all earlier implementation tasks
- [ ] T325 [P] Write the rollout and measurement notes: the deployment order (you apply the migration, then deploy, then check the startup line), the read-only queries that measure SC-001 over 7 days (background requests per day from the ledger) and SC-004, and the rollback steps — `docs/operations-hardening.md` (EXTENDED) — Depends: T322
- [ ] T326 **[OWNER]** After the first week of measurements, decide whether to return `MONITORING_INTERVAL_MINUTES` on the VM from 360 to 60 and record SC-001 and SC-007 results in the runbook — `docs/operations-hardening.md` (EXTENDED) — Depends: T324, T325

---

## Dependencies & execution order

- **Phase 1 -> Phase 2 -> everything else.** Phase 2 includes the migration file (T260); you apply it before running DB-backed tests.
- **US2 (Phase 3)** needs only Phase 2. **US1 (Phase 4)** needs Phase 2 and the exceptions from T261; it can be developed in parallel with US2's tests but its connector edit (T284) should follow T272.
- **US4 (Phase 5)** needs US2 (exceptions, gate) and US1's connector change (T290 after T284).
- **US3 (Phase 6)** needs US1 (state, unchanged branch). **US6 (Phase 7)** needs US2 (ledger) and US3 (cadence defaults for the startup line).
- **US5 (Phase 8)** needs US2 (gate), US1/US4 (connector), and the cadence schema validator (T298). T319 to T321 are owner-gated and last.
- **Files edited more than once, in this order**: `official_fetch.py` T284 -> T290 -> T316; `source_monitor/graph.py` T274 -> T285 -> T292 -> T299; `scheduler.py` T300 -> T307; `schemas/source.py` T263 -> T298 -> T314; `openapi.yaml` (003) T276 -> T293 -> T301.

### Parallel examples

- Phase 2 tests: T253, T254, T255, T256, T257 together; then T258, T259, T261, T262 together.
- US2 tests: T264, T265, T266, T267, T268, T269, T270 together.
- US1 tests: T277, T278, T279, T280, T281 together; US4 tests T287, T288, T289 together; US6 tests T302 to T305 together; US5 tests T310 to T313 together.

## Implementation strategy

1. **MVP = Phases 1 to 4 (setup, foundation, US2, US1).** After that, the gate exists and monitoring stops spending on unchanged pages: the estimated 48 requests a day drop to a handful (estimate). Stop and verify here before continuing; this is the first point worth deploying.
2. Add US4 (Phase 5) next, so provider failures stop corrupting health before more monitoring-related work lands.
3. Then US3, US6, and finally US5, whose last steps wait for your decisions.
4. Each phase ends with a green CI run and, where noted, a contract-drift check. Merge phases to `main` one branch at a time; no phase is merged without CI green on the branch.

## Summary

| Phase | Story | Tasks | Count |
|---|---|---|---|
| 1 | Setup | T251-T252 | 2 |
| 2 | Foundational | T253-T263 | 11 |
| 3 | US2 (P1) | T264-T276 | 13 |
| 4 | US1 (P1) | T277-T286 | 10 |
| 5 | US4 (P2) | T287-T293 | 7 |
| 6 | US3 (P2) | T294-T301 | 8 |
| 7 | US6 (P2) | T302-T309 | 8 |
| 8 | US5 (P3) | T310-T321 | 12 |
| 9 | Polish | T322-T326 | 5 |
| | **Total** | T251-T326 | **76** |
