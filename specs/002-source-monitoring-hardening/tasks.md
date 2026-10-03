# Tasks: Source Monitoring, Coverage Analytics & Production Hardening

**Input**: `specs/002-source-monitoring-hardening/` — plan.md, spec.md, data-model.md, contracts/openapi.yaml, quickstart.md, research.md
**Prerequisites**: plan.md, spec.md (both present). Continues the T-id sequence from `specs/001-scholarship-mvp/tasks.md` (last id T140) — **first id here is T141**.
**Tests**: MANDATORY (plan.md Technical Context). Every implementation task is preceded by a test task that MUST be written first and MUST fail before the implementation task starts.
**Migrations**: Tasks below only *generate* Alembic migration files. **No task runs `alembic upgrade`** — you run all migrations yourself. DB-backed tests will fail until you have applied them; that is expected and called out where it matters.

## Format: `- [ ] T### [P?] [US?] Description — path (NEW|EXTENDED) — Satisfies: …`

- **[P]**: different file, no dependency on an incomplete task (same rule as 001).
- **[USn]**: user-story phases only (Setup, Foundational, Polish carry no story label).
- **NEW / EXTENDED**: whether the file is created by this feature or gains additive code in an existing file (existing signatures never change).
- All paths are relative to the repo root `scholarship-ai-assistant/`.

## Hard rules baked into every task (from plan.md / constitution)

- `source_monitor` is a **deterministic LangGraph workflow** under `backend/app/workflows/` — never an agent, never an LLM call (FR-MON-5).
- Re-matching calls the existing `backend/app/services/matching.py::match_scholarship` — no second matching implementation, no LLM in hard-constraint evaluation (FR-ALERT-1/2, Principle II).
- Candidate validation has **no write path** to `source_registry` or `candidate_sources.status` (FR-CANDVAL-2/3, Principle IV).
- Nothing under `specs/001-scholarship-mvp/` is modified.

## Deviations from plan.md's file list and ordering (read before starting)

- **D-1 (extra files for the T140 fix).** plan.md says the listing-page fix lives in `official_fetch.py`. Reading the code shows the actual T140 defect is in `backend/app/agents/discovery/agent.py` (`_run_web_listing_step` hardcodes `f"https://{source.domain}"`), and the URL is passed through `backend/app/tools/listing_fetch.py`. The fix therefore touches those two files (EXTENDED) plus the seed loader and seed YAML, which plan.md does not list. The URL-resolution logic itself still lives in `official_fetch.py`, as planned.
- **D-2 (phase order).** US6 is pulled forward to Phase 3, ahead of US1, because monitoring's change-detection depends on fetching the correct page. The remaining stories follow spec priority: US1, US2, US3 (P1), then US4, US5, US8 (P2), then US7 (P3). US6 and US8 are P2 but US6 sits first by dependency.
- **D-3 (lifecycle logic placement).** `services/lifecycle.py` is in Foundational (not US5) because US1 (stale/closed/reopened), US3 (source_unavailable) and US5 all consume it. US5's own phase covers the remaining wiring plus the end-to-end lifecycle walk.
- **D-4 (001 task file untouched).** These tasks close the *intent* of 001's T140, but `specs/001-scholarship-mvp/tasks.md` is not modified, so its T140 checkbox stays unchecked until you decide to tick it.

## Decisions (all resolved)

- **O-1 — RESOLVED: add read tracking.** An additive nullable `read_at` (timestamptz) column is added to `alerts` so `GET /alerts?unread_only=` can filter on it. Included in T145 (model test), T151 (model), T155 (migration (b)); the spec-doc updates are T249; the filter is T195 (now unblocked). No existing column changes. Mark-read (T250, `POST /alerts/{id}/read`) is what makes `unread_only` functional: it is the only thing that sets `read_at`, so without it every alert would read as unread.
- **O-2 — RESOLVED: recovery required.** A source flagged `failing` stays in the normal monitoring schedule (it is not permanently excluded) and a later successful fetch clears its failing status back to `active`. Test coverage in T198; implementation is T204 (unblocked, last task in US3).
- **O-3 — CONFIRMED (default kept).** A record leaving `stale` or `source_unavailable` after a successful re-check becomes `verified` when its details are confirmed (its `verification_status` is verified) and `unverified` when not fully confirmed. Made explicit in T148 (tests), T158 (service) and T213 (graph wiring).
- **O-4 — RESOLVED: authenticate metrics.** `/metrics` requires bearer authentication via the existing auth dependency, in addition to Caddy blocking it from public ingress. Test in T218, implementation in T227/T231, deploy tests T220, Caddy T232. **Contract follow-up note:** `contracts/openapi.yaml` already inherits the global `bearerAuth` for `/metrics` (no per-operation override), so no contract edit is required; adding an explicit `security: [bearerAuth: []]` and a `401` response on `/metrics` is optional clarity and is not tasked. A Prometheus scraper must be configured with a bearer token for an operator/service user.
- **Interpretation (not blocking).** A record is moved to `closed` only when the fetch succeeded **and** extraction returned grounded results **and** the record is absent from or marked closed in that listing. A failed or empty extraction never closes anything (FR-HEALTH-5, FR-LIFECYCLE-3).

---

## Phase 1: Setup

**Purpose**: dependencies, configuration surface.

- [ ] T141 Add `APScheduler` and `prometheus-client` to runtime dependencies and `pip-audit` to the dev dependency group, then refresh the lockfile (`uv lock`) — `backend/pyproject.toml` (EXTENDED) — Satisfies: plan.md Technical Context; NFR-HARDEN-2/4/8
- [ ] T142 [P] Add the new optional variables with safe defaults (`SMTP_HOST`, `SMTP_PORT=587`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERTS_FROM_EMAIL`, `MONITORING_INTERVAL_MINUTES=60`, `FETCH_LOG_RETENTION_DAYS=90`, `HEALTH_WINDOW_N=5`, `RATE_LIMIT_PER_MINUTE=120`, `STORAGE_ROOT=/data/storage` comment) exactly as listed in quickstart.md — `.env.example` (EXTENDED) — Satisfies: NFR-HARDEN-8 (no secrets in VCS), quickstart.md "New environment variables"
- [ ] T143 [P] Write a failing test that `Settings()` loads with none of the new variables set and exposes the documented defaults (interval 60, retention 90, health window 5, rate limit 120, SMTP fields empty/None, port 587) — `backend/tests/core/test_config_002.py` (NEW; create `backend/tests/core/__init__.py` if absent) — Satisfies: spec Assumptions (operational config, not fixed values)
- [ ] T144 Add the settings to the existing settings class, all optional with those defaults; do not change any existing setting — `backend/app/core/config.py` (EXTENDED) — Depends: T143 — Satisfies: FR-MON-1, FR-HEALTH-4, NFR-HARDEN-2/7

---

## Phase 2: Foundational (blocks every user story)

**Purpose**: schema, ORM models, migration files, shared schemas/repo changes, and the pure lifecycle transition service. Nothing in Phases 3+ may start before this phase is complete.

### Tests first (must fail before the implementations below)

- [ ] T145 [P] Write failing tests asserting `Base.metadata` contains: `source_registry.listing_page_url` (text, nullable) and `freshness_window_days` (int, nullable); `source_fetch_log.fetched_url` (nullable), `used_homepage_fallback` (bool, not null, server default false), `monitoring_run_id` (UUID FK → `monitoring_runs.id`, nullable); tables `monitoring_runs`, `alerts`, `alert_preferences`, `candidate_source_validations` with the columns/types/nullability/FKs in data-model.md §4–7; `monitoring_runs.trigger` enum {scheduled, manual} and `status` enum {running, completed, failed}; `alert_preferences.user_id` is the PK; `alerts.read_at` (timestamptz, nullable, no default — O-1) — `backend/tests/data/test_002_models.py` (NEW) — Satisfies: data-model.md §1–7
- [ ] T146 [P] Write failing static tests (no DB connection) that parse the two new revision files and assert: both exist; revision (a)'s `down_revision` is the current head `ad4826bd57e6` (verify with the read-only `alembic heads` before writing the test); revision (b)'s `down_revision` is revision (a); the migration chain has exactly one head; upgrades contain only additive operations (no `drop_table`, `drop_column`, `alter_column` type change, enum value removal); every `upgrade` has a matching `downgrade`; `monitoring_runs` is created before the FK column that references it — `backend/tests/data/test_002_migrations.py` (NEW) — Satisfies: data-model.md header + Cross-cutting "Migrations"
- [ ] T147 [P] Write failing tests that `source_repo.log_fetch` persists `fetched_url`, `used_homepage_fallback`, `monitoring_run_id` (and defaults them to `None` / `False` / `None` when omitted), and that `source_repo.upsert_source_by_domain` persists `listing_page_url` and `freshness_window_days` and a later upsert with a different `listing_page_url` replaces it — `backend/tests/data/test_source_repo_002.py` (NEW) — Satisfies: FR-FETCH-2, FR-FETCH-3, FR-MON-7
- [ ] T148 [P] Write failing tests for the pure lifecycle service: `evaluate_freshness(scholarship, source, now)` returns `stale` only when the per-source `freshness_window_days` (or `DEFAULT_FRESHNESS_WINDOW_DAYS`) has elapsed without a successful re-check, else `None`; `evaluate_fetch_outcome(...)` returns `source_unavailable` on retry exhaustion and **never** `expired`/`closed` for a failed fetch; closed→`reopened` (never `newly_discovered`); unverified→`verified` on confirmed re-check; unchanged input returns `None` (no spurious write); table-driven over all nine `LifecycleStatus` values; `stale`→`verified` and `source_unavailable`→`verified` when details are confirmed, and `stale`→`unverified` / `source_unavailable`→`unverified` when not fully confirmed (O-3); plus an AST test asserting `services/lifecycle.py` imports nothing from `app.agents`, `app.core.llm`, `langchain*`, or `google.genai` — `backend/tests/services/test_lifecycle.py` (NEW) — Satisfies: FR-LIFECYCLE-1..5, US1 Scenario 5

### Implementation

- [ ] T149 Add `listing_page_url`, `freshness_window_days` to `SourceRegistry` and `fetched_url`, `used_homepage_fallback`, `monitoring_run_id` to `SourceFetchLog`; change no existing column or enum — `backend/app/models/source.py` (EXTENDED) — Depends: T145 — Satisfies: data-model.md §1–2
- [ ] T150 [P] Create `MonitoringRun` ORM model plus `MonitoringTrigger` and `MonitoringStatus` enums, system-owned (no `user_id`), `server_default=now()` on `started_at` — `backend/app/models/monitoring.py` (NEW) — Depends: T145 — Satisfies: data-model.md §4, FR-MON-1
- [ ] T151 [P] Create `Alert` (user-scoped, indexed `user_id`/`scholarship_id`, JSONB `change_summary`/`channels_requested`, nullable `read_at` timestamptz — O-1) and `AlertPreferences` (PK `user_id`) ORM models — `backend/app/models/alert.py` (NEW) — Depends: T145 — Satisfies: data-model.md §5–6, FR-ALERT-3/4/5
- [ ] T152 [P] Create `CandidateSourceValidation` ORM model (append-only, indexed `candidate_source_id`, JSONB `official_signals` default `{}`) — `backend/app/models/candidate_validation.py` (NEW) — Depends: T145 — Satisfies: data-model.md §7, FR-CANDVAL-1
- [ ] T153 Register the three new model modules in the eager-import line so SQLAlchemy resolves relationships — `backend/app/models/__init__.py` (EXTENDED) — Depends: T150, T151, T152
- [ ] T154 Generate migration (a) by hand (do **not** run it): additive columns on `source_registry` and `source_fetch_log` (`used_homepage_fallback` with `server_default=false`, all others nullable) and create `monitoring_runs` **before** adding the `source_fetch_log.monitoring_run_id` FK; `down_revision = "ad4826bd57e6"` (confirm with `alembic heads`); full `downgrade` — `backend/migrations/versions/<new_rev>_add_monitoring_columns_and_runs.py` (NEW) — Depends: T149, T150, T146 — Satisfies: data-model.md §1, §2, §4
- [ ] T155 Generate migration (b) by hand (do **not** run it): create `alerts` (including the nullable `read_at` timestamptz column — O-1, additive, no default), `alert_preferences`, `candidate_source_validations` with the indexes/FKs/defaults in data-model.md; `down_revision` = migration (a)'s revision; full `downgrade` — `backend/migrations/versions/<new_rev>_add_alerts_and_candidate_validations.py` (NEW) — Depends: T154, T151, T152 — Satisfies: data-model.md §5–7, O-1 (see T249)
- [ ] T156 [P] Add `listing_page_url` and `freshness_window_days` to the registry create/read schemas and `fetched_url`, `used_homepage_fallback`, `monitoring_run_id` to `SourceFetchLogCreate`/`SourceFetchLogRead` (all optional with the defaults above; existing fields untouched) — `backend/app/schemas/source.py` (EXTENDED) — Depends: T147 — Satisfies: FR-FETCH-2/3, FR-MON-7
- [ ] T157 Make `log_fetch` persist the three new fetch-log fields and `upsert_source_by_domain` persist/replace `listing_page_url` and `freshness_window_days`; no existing function's behavior changes for 001 callers — `backend/app/data/repositories/source_repo.py` (EXTENDED) — Depends: T147, T156, T149 — Satisfies: FR-FETCH-2/3, FR-MON-7
- [ ] T158 Implement `evaluate_freshness`, `evaluate_fetch_outcome`, and `DEFAULT_FRESHNESS_WINDOW_DAYS` as pure, deterministic functions returning `LifecycleStatus | None`, with transition rules encoded as data in the style of `services/verification.py::compute_lifecycle_status` (reuse, do not replace it); apply the confirmed O-3 rule for exits from `stale`/`source_unavailable`: after a successful re-check the record becomes `verified` if its details are confirmed (`verification_status` is verified), else `unverified` — never `newly_discovered`, `closed` or `expired` — `backend/app/services/lifecycle.py` (NEW) — Depends: T148 — Satisfies: FR-LIFECYCLE-1..5, FR-MON-3

- [ ] T249 [P] Record the O-1 `read_at` column in the spec docs, additively: add `read_at` (timestamptz, nullable; set when the owner has read the alert) to the `alerts` table in `data-model.md` §5, and add `read_at: { type: string, format: date-time, nullable: true }` to the `Alert` schema in `contracts/openapi.yaml`; **also add the `POST /alerts/{id}/read` operation to `contracts/openapi.yaml` (tag `alerts`, path param `id` uuid, `200` returns the updated `Alert`, `404` when the alert does not exist or belongs to another user) so the contract stays in sync with T250**; no other change to either file; do not touch `specs/001-scholarship-mvp/` — `specs/002-source-monitoring-hardening/data-model.md` (EXTENDED), `specs/002-source-monitoring-hardening/contracts/openapi.yaml` (EXTENDED) — Satisfies: O-1 (cross-referenced from T151, T155, T186, T195, T250; the T246 drift check expects it)

**Checkpoint**: the foundational tests now pass once you have applied the two migrations yourself (`alembic upgrade head`). User stories may begin.

---

## Phase 3: User Story 6 — Monitoring and discovery fetch the right page (P2, pulled forward — D-2)

**Goal**: both monitoring and discovery fetch the configured listing page; homepage is a recorded fallback. This is the T140 defect fix.
**Independent test**: configure one source with a listing page ≠ homepage; run discovery; the `source_fetch_log.fetched_url` is the listing page, `used_homepage_fallback=false`; clear it → homepage + `true`; change it → next fetch uses the new URL (quickstart US6).

### Tests first

- [ ] T159 [P] [US6] Write failing tests for `resolve_fetch_target(source)`: precedence is `listing_page_url` column → legacy `extraction_rules["list_page_url"]` → `https://{domain}`; `used_homepage_fallback` is true only on the last branch; changing the column changes the result on the very next call (no caching of the URL); a listing URL that returns 404, or (if `FetchResult` in `backend/app/tools/web_fetch.py` exposes a final URL) redirects to the homepage, is logged as a `fail`/anomaly and never treated as "the homepage was intended" — `backend/tests/sources/test_listing_url_resolution.py` (NEW) — Satisfies: US6 Scenarios 1–4, FR-FETCH-1/2, FR-MON-7, Edge Case "listing page 404"
- [ ] T160 [P] [US6] Extend connector tests: every `source_fetch_log` row written by `fetch_official`/`fetch_and_extract_listing` (success **and** failure) carries `fetched_url` and `used_homepage_fallback`; calling `fetch_and_extract_listing(db, source_id, url)` with an explicit positional URL still behaves exactly as in 001 — `backend/tests/sources/test_connectors.py` (EXTENDED) — Satisfies: FR-FETCH-3, SC-005, 001 backward compatibility
- [ ] T161 [P] [US6] Add a T140 regression test: `_run_web_listing_step` must not pass `f"https://{source.domain}"` to the listing-fetch tool when a listing URL is configured; it delegates URL choice to the connector; existing AST guardrails (agent never touches `source_repo`) keep passing — `backend/tests/agents/test_discovery_agent.py` (EXTENDED) — Satisfies: US6 Scenario 2, FR-FETCH-1
- [ ] T162 [P] [US6] Write failing tests that the seed loader maps each seed's `extraction_rules.list_page_url` into the `listing_page_url` column, and that DAAD, Stipendium Hungaricum and KAUST seed with `discovery_role: true` after this feature — `backend/tests/services/test_source_registry_seed_002.py` (NEW) — Satisfies: FR-FETCH-1, closes 001 T140 intent
- [ ] T163 [P] [US6] Write the end-to-end integration test following quickstart US6 steps 1–4 with fixture content (no live network): seeded source with a distinct listing page → fetch-log rows show the listing URL; cleared → homepage + fallback true; changed → new URL on next run — `backend/tests/integration/test_listing_page_fetch_flow.py` (NEW) — Satisfies: US6 independent test, SC-005

### Implementation

- [ ] T164 [US6] Add `resolve_fetch_target(source)` returning `(url, used_homepage_fallback)` with the precedence above; thread optional `fetched_url`, `used_homepage_fallback`, `monitoring_run_id` kwargs into the `log_fetch` calls inside `fetch_official`; make `fetch_and_extract_listing`'s `url` argument optional (`None` → resolve) while keeping the existing positional call valid — `backend/app/sources/connectors/official_fetch.py` (EXTENDED) — Depends: T159, T160, T157 — Satisfies: FR-FETCH-1/2/3, FR-MON-7
- [ ] T165 [US6] Let the agent-facing wrapper call the connector without a caller-built URL (url optional, forwarded as `None`) — `backend/app/tools/listing_fetch.py` (EXTENDED) — Depends: T164 — Satisfies: FR-FETCH-1 (D-1)
- [ ] T166 [US6] Fix T140: in `_run_web_listing_step` stop building `f"https://{source.domain}"`; call `listing_fetch` letting the connector resolve the target; keep the agent free of any `source_repo` access — `backend/app/agents/discovery/agent.py` (EXTENDED) — Depends: T161, T165 — Satisfies: US6 Scenario 2, FR-FETCH-1 (D-1)
- [ ] T167 [US6] Map `extraction_rules.list_page_url` → `listing_page_url` when seeding (keep the legacy key for back-compat) — `backend/app/services/source_registry_seed.py` (EXTENDED) — Depends: T162, T157 — Satisfies: FR-FETCH-1 (D-1)
- [ ] T168 [US6] Set `listing_page_url` for each seeded source from its `list_page_url`, flip `discovery_role` to `true` for DAAD, Stipendium Hungaricum and KAUST, and remove the "dead metadata / discovery_role false" explanatory comments that no longer apply — `backend/app/data/seeds/seed_sources.yaml` (EXTENDED) — Depends: T167, T166 — Satisfies: closes 001 T140 intent (D-1, D-4)
- [ ] T169 [US6] Run `u6_test_*` tests green, then record in the test docstring or commit notes that the weekly `pytest -m smoke` live listing run (`backend/tests/smoke/test_discovery_listing_extraction_live.py`) is the real-network check for the three re-enabled sources — you run it; this task only confirms the mocked suite passes — `backend/tests/integration/test_listing_page_fetch_flow.py` (EXTENDED) — Depends: T168, T163 — Satisfies: SC-005

**Checkpoint**: US6 independently verifiable; monitoring can now rely on correct-page fetching.

---

## Phase 4: User Story 1 — Scheduled monitoring catches a changed scholarship (P1) 🎯 MVP

**Goal**: a recurring, lock-guarded, deterministic workflow re-checks active sources and routes every change through the existing ingestion workflow.
**Independent test**: seed one active source with a stored scholarship; alter fixture content (new scholarship + changed deadline); `POST /monitoring/runs`; assert `changes_detected=2`, new record `newly_discovered`, existing record `updated` with prior value auditable; re-run with no change → `changes_detected=0`, `last_checked_at` advances, nothing new (quickstart US1).

### Tests first

- [ ] T170 [P] [US1] Write failing workflow tests: scenarios 1–5 from spec US1; every detected change goes through `run_ingestion` (assert it is the function called — FR-MON-4); the listing TTL cache is bypassed during monitoring; closed/reopened follow the interpretation rule above; stale sweep marks overdue records; a source disabled mid-run is skipped and excluded next run; each fetch-log row carries `monitoring_run_id`; run counters (`sources_processed`, `sources_failed`, `changes_detected`) are correct; `ended_at` set exactly once — `backend/tests/workflows/test_source_monitor.py` (NEW) — Satisfies: FR-MON-1..4, FR-LIFECYCLE-2, US1 Scenarios 1–5
- [ ] T171 [P] [US1] Write AST/import guardrail tests that `app/workflows/source_monitor/` imports nothing from `app.agents`, `app.core.llm`, `langchain*`, `google.genai`, and that no agent module or agent registry gained a `source_monitor` entry — `backend/tests/workflows/test_source_monitor_guardrails.py` (NEW) — Satisfies: FR-MON-5, plan Agent Architecture constraint
- [ ] T172 [P] [US1] Write failing tests for the scheduler and lock helper together: Postgres advisory lock is exclusive per source (second concurrent acquire for the same source returns "not acquired", a different source is unaffected, lock released on exception); scheduler registers one interval job from `MONITORING_INTERVAL_MINUTES` and is not started when the interval is ≤ 0 — `backend/tests/scheduling/test_scheduler_and_locks.py` (NEW; create `backend/tests/scheduling/__init__.py`) — Satisfies: FR-MON-1, FR-MON-6
- [ ] T173 [P] [US1] Write failing API tests per contract: `GET /monitoring/runs` (default limit 20, max 100, newest first), `GET /monitoring/runs/{run_id}` (detail with `source_outcomes`, 404 on unknown), `POST /monitoring/runs` → 202 with a `MonitoringRun` body and 409 when a run is already in progress; bearer auth required — `backend/tests/api/test_monitoring.py` (NEW) — Satisfies: FR-MON-1, FR-MON-6, contracts/openapi.yaml `/monitoring/*`

### Implementation

- [ ] T174 [P] [US1] Create `MonitoringRun` and `MonitoringRunDetail` response schemas matching the contract (detail embeds `source_outcomes: list[SourceFetchLogEntry]`) — `backend/app/schemas/monitoring.py` (NEW) — Depends: T156
- [ ] T175 [P] [US1] Create the monitoring repository: `create_run`, `finish_run(status, counters)` setting `ended_at` exactly once, `list_runs(limit)`, `get_run`, `get_run_outcomes` (fetch-log rows by `monitoring_run_id`), `has_running_run` — `backend/app/data/repositories/monitoring_repo.py` (NEW) — Depends: T150, T157
- [ ] T176 [P] [US1] Create the advisory-lock helper (context manager over `pg_try_advisory_lock`, keyed by a stable hash of the source id; always releases) and the package marker — `backend/app/scheduling/__init__.py`, `backend/app/scheduling/locks.py` (NEW) — Depends: T172 — Satisfies: FR-MON-6
- [ ] T177 [US1] Add a `bypass_cache: bool = False` keyword to `fetch_and_extract_listing` so monitoring never receives the `cached` outcome (a cached result cannot detect change); default behavior for 001 callers unchanged — `backend/app/sources/connectors/official_fetch.py` (EXTENDED) — Depends: T164, T170 — Satisfies: FR-MON-2/3
- [ ] T178 [US1] Create the workflow skeleton: load active sources → per source acquire the advisory lock (skip if held) → resolve/fetch via `official_fetch` with `monitoring_run_id` and `bypass_cache=True` → record the outcome → update `source_registry.last_checked_at` → update run counters; no LLM, no agent imports — `backend/app/workflows/source_monitor/__init__.py`, `backend/app/workflows/source_monitor/graph.py` (NEW) — Depends: T170, T171, T175, T176, T177, T158 — Satisfies: FR-MON-1/2/5/6
- [ ] T179 [US1] Add change detection: compare extracted candidates with stored scholarships for the source; new → `run_ingestion` then `newly_discovered`; changed deadline/requirement/funding → `run_ingestion` (prior values stay auditable via the existing field/conflict trail) then `updated`; absent-or-closed in a successful grounded listing → `closed`; previously closed and listed open → `reopened`; apply transitions only when `services/lifecycle.py` returns non-`None`; increment `changes_detected` — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T178 — Satisfies: FR-MON-3/4, FR-LIFECYCLE-4/5, US1 Scenarios 1–3, 5
- [ ] T180 [US1] Add the stale sweep: after fetching, call `evaluate_freshness` for each stored scholarship of the source and transition overdue records to `stale` — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T179 — Satisfies: FR-LIFECYCLE-2, US1 Scenario 4, SC-004
- [ ] T181 [US1] Create the monitoring router: `GET /monitoring/runs`, `GET /monitoring/runs/{run_id}`, `POST /monitoring/runs` (202, runs `source_monitor` with `trigger=manual`; 409 if `has_running_run`) — `backend/app/api/monitoring.py` (NEW) — Depends: T173, T174, T175, T180 — Satisfies: contracts `/monitoring/*`, FR-MON-6
- [ ] T182 [US1] Create the APScheduler setup/teardown: one interval job (`MONITORING_INTERVAL_MINUTES`) invoking `source_monitor` with `trigger=scheduled`; `max_instances=1`, `coalesce=True` — `backend/app/scheduling/scheduler.py` (NEW) — Depends: T172, T180, T144 — Satisfies: FR-MON-1
- [ ] T183 [US1] Register the monitoring router and start/stop the scheduler in the existing lifespan (skipped when interval ≤ 0 or under test) — `backend/app/main.py` (EXTENDED) — Depends: T181, T182

**Checkpoint**: US1 independently testable and demonstrable (MVP).

---

## Phase 5: User Story 2 — Re-match and alert the affected owner (P1)

**Goal**: detected changes are re-matched through the existing deterministic path and owners are alerted in-app and by email, with pluggable channels.
**Independent test**: seed a profile newly eligible after the US1 change; run monitoring; `GET /alerts` shows one alert (both channels); flip email off → next alert in-app only; a non-impacting change → zero alerts (quickstart US2).

### Tests first

- [ ] T184 [P] [US2] Write failing service tests: alert only when the verdict is newly favorable or materially changed versus the owner's latest prior match (`match_repo.latest_for_user_and_scholarship`); zero alerts and zero rows for a non-impacting change (FR-ALERT-6); `channels_requested` snapshots current `alert_preferences` (absent row → both enabled); both channels off → no alert row and no send; a fake third `NotificationChannel` can be registered without touching generation logic (US2 Scenario 5); an email failure is stored in `email_error`, never raises, and a retry updates the same row; unset `SMTP_HOST` records an explicit `email_error` instead of silently skipping; AST test that `services/alerts.py` imports no LLM modules — `backend/tests/services/test_alerts.py` (NEW) — Satisfies: FR-ALERT-3..6, Edge Cases (alert failure, alerts disabled)
- [ ] T185 [P] [US2] Write failing tests that the re-match step in `source_monitor` calls `app.services.matching.match_scholarship` (monkeypatch spy asserting it is the *same function object* the on-demand match route uses), produces a verdict equal to the on-demand path for identical inputs, and that no LLM module is invoked — `backend/tests/workflows/test_source_monitor_rematch.py` (NEW) — Satisfies: FR-ALERT-1/2, Principle II, US2 Scenarios 1–2
- [ ] T186 [P] [US2] Write failing API tests: `GET /alerts` returns only the caller's alerts (another user's alerts never visible — same isolation pattern as 001 T132); `GET /alerts/preferences` returns defaults when no row exists; `PUT /alerts/preferences` partial update persists and returns the full object; `GET /alerts?unread_only=true` returns only alerts whose `read_at` is NULL while the default (`false`) returns all, and `read_at` is serialized in the `Alert` response (O-1) — `backend/tests/api/test_alerts_api.py` (NEW) — Satisfies: FR-ALERT-5, contracts `/alerts*`
- [ ] T187 [P] [US2] Write the end-to-end SC-001 test: fixture content change → monitoring run → affected record updated → owner has an in-app alert and an email send attempt, within one run, with no manual step — `backend/tests/integration/test_monitoring_alert_flow.py` (NEW) — Satisfies: SC-001, US2 Scenario 3

### Implementation

- [ ] T188 [P] [US2] Create `Alert`, `AlertPreferences`, `AlertPreferencesUpdate` schemas matching the contract — `backend/app/schemas/alert.py` (NEW) — Depends: T151
- [ ] T189 [P] [US2] Create the alert repository (user-scoped create/list/get, preferences get-or-default/upsert, delivery-status update on the same row) — `backend/app/data/repositories/alert_repo.py` (NEW) — Depends: T151
- [ ] T190 [US2] Implement the `NotificationChannel` Protocol (mirroring the `StorageBackend` Protocol style), `InAppChannel` (sets `delivered_in_app_at`) and `EmailChannel` (stdlib `smtplib`, no new dependency; failures captured to `email_error`) — `backend/app/services/alerts.py` (NEW) — Depends: T184, T189, T144 — Satisfies: FR-ALERT-4, OD-8
- [ ] T191 [US2] Implement owner selection and alert generation in the same module: for a **new** scholarship, re-match every owner with a profile; for a **changed** one, re-match owners with a prior `matches` row or an `Application` for it; call `match_scholarship` (never a reimplementation); generate an alert only on newly-favorable or materially-changed outcome, with `change_summary` `{kind, fields}`; dispatch only to enabled channels — `backend/app/services/alerts.py` (EXTENDED) — Depends: T190, T185 — Satisfies: FR-ALERT-1/2/3/5/6
- [ ] T192 [US2] Add the re-match/alert step to the workflow after a change is stored; an alert-step exception is logged and counted but never rolls back the stored scholarship change (Edge Case) — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T191, T180 — Satisfies: FR-ALERT-1, Edge Case "re-match/alert fails after change stored"
- [ ] T193 [US2] Create the alerts router: `GET /alerts`, `GET /alerts/preferences`, `PUT /alerts/preferences`, all scoped to the authenticated user via the existing auth dependency — `backend/app/api/alerts.py` (NEW) — Depends: T186, T188, T189
- [ ] T194 [US2] Register the alerts router — `backend/app/main.py` (EXTENDED) — Depends: T193, T183
- [ ] T195 [US2] Implement the `unread_only` filter on `GET /alerts` (O-1 resolved): when true, return only the caller's alerts with `read_at IS NULL`; include `read_at` in the response (extend the alert schema and repository list query as needed); no mark-read endpoint is added (none is in the contract) — `backend/app/api/alerts.py` (EXTENDED), `backend/app/schemas/alert.py` (EXTENDED), `backend/app/data/repositories/alert_repo.py` (EXTENDED) — Depends: T193, T186, T249 — Satisfies: contracts `/alerts` `unread_only`, O-1
- [ ] T196 [US2] Make the T187 end-to-end test pass, verifying the full re-match-and-alert flow with fixtures only (no SMTP server; inject a fake email channel): a seeded active source and an eligible owner profile; a fixture listing with one new scholarship and one changed deadline; a single monitoring run updates the stored records (`newly_discovered` / `updated`, prior deadline still auditable), re-matches through `match_scholarship` with a verdict equal to the on-demand path, and creates exactly one alert for the owner with `delivered_in_app_at` set and one email send attempt recorded; with `email_enabled=false` the next qualifying change yields an in-app-only alert; a change affecting no owner creates no alert; a failing email channel records `email_error` without failing the run or rolling back the record update — `backend/tests/integration/test_monitoring_alert_flow.py` (EXTENDED) — Depends: T194, T187
- [ ] T250 [US2] Add the mark-read endpoint `POST /alerts/{id}/read` (O-1, FR-ALERT in-app alert lifecycle): sets `alerts.read_at` to now for the authenticated owner's own alert and returns the updated `Alert`; user-scoped like every other alert route — another user's alert id or an unknown id returns 404 (never reveals existence); idempotent (calling again keeps the original `read_at`); once set, the alert is excluded by `GET /alerts?unread_only=true` (this is what makes T195 functional). **Test first:** write the failing tests (own alert marks read and `read_at` is set; second call leaves `read_at` unchanged; other user's alert → 404 and its `read_at` untouched; the alert then disappears from `unread_only=true` but remains in the default list; unauthenticated → 401) in `backend/tests/api/test_alerts_api.py` (EXTENDED) and confirm they fail before implementing — `backend/app/api/alerts.py` (EXTENDED), `backend/app/data/repositories/alert_repo.py` (EXTENDED, user-scoped `mark_read(db, user_id, alert_id)`), `backend/app/schemas/alert.py` (EXTENDED, `read_at` on the `Alert` response) — Depends: T193, T195, T249 — Satisfies: O-1, FR-ALERT-3/4 (in-app channel), contracts `POST /alerts/{id}/read` (added by T249)

**Checkpoint**: US1 + US2 deliver the full catch-it-and-tell-me loop.

---

## Phase 6: User Story 3 — See source health, not silent failure (P1)

**Goal**: a failed fetch is always distinguishable from a successful zero-change fetch, on a per-source health view.
**Independent test**: one source's fetch raises, another succeeds with zero changes; `GET /sources/health` and `GET /sources/{id}/fetch-log` distinguish `fail` from `ok, items_found=0` (quickstart US3).

### Tests first

- [ ] T197 [P] [US3] Write failing API tests: `GET /sources/health` returns every registry source (including disabled) with `status`, `last_checked_at`, `last_success_at`, `consecutive_failures`, `listing_page_url`; `?status=` filter; a failed fetch never produces a "zero results" success shape; `GET /sources/{id}/fetch-log` is newest-first, honors `limit` (default 50, max 200), exposes `fetched_url`/`used_homepage_fallback`, 404 on unknown id; the `/sources/health` route is not shadowed by any `/sources/{…}` route in `api/sources.py` — `backend/tests/api/test_health.py` (NEW) — Satisfies: FR-HEALTH-1..4, FR-FETCH-3, contracts `/sources/health`, `/sources/{id}/fetch-log`
- [ ] T198 [US3] Extend the workflow tests: a source whose fetch times out is recorded `fail` with reason and retried per the existing retry policy before being marked failing; a successful zero-change fetch is `ok, items_found=0`; a listing whose extraction yields nothing preserves the prior good scholarship records untouched and flags the source for re-check; consecutive-failure count reaches the configured N and the source reads as failing; **recovery (O-2)**: a source with `status=failing` is still selected by the next scheduled run (not permanently excluded), one subsequent successful fetch sets it back to `active` with `consecutive_failures` reading 0, and a further failed fetch leaves it `failing` — `backend/tests/workflows/test_source_monitor.py` (EXTENDED) — Depends: T170 — Satisfies: FR-HEALTH-1/2/4/5/6, US3 Scenarios 1–4

### Implementation

- [ ] T199 [P] [US3] Create `SourceHealth` and `SourceFetchLogEntry` response schemas matching the contract — `backend/app/schemas/health.py` (NEW) — Depends: T156
- [ ] T200 [US3] Add read helpers: `consecutive_failure_count(db, source_id)`, `last_success_at(db, source_id)`, `list_source_health(db, status=None)`, and a limit-bounded newest-first fetch-log query; no existing function's behavior changes — `backend/app/data/repositories/source_repo.py` (EXTENDED) — Depends: T197, T157 — Satisfies: FR-HEALTH-3/4
- [ ] T201 [US3] Create the health router (`GET /sources/health`, `GET /sources/{source_id}/fetch-log`); ensure it is included **before** the existing sources router so `/sources/health` is not captured by a path parameter — `backend/app/api/health.py` (NEW) — Depends: T199, T200
- [ ] T202 [US3] In the workflow, on extraction failure or empty/ungrounded extraction: write a `fail` fetch-log row, leave existing scholarship rows untouched (never overwrite with empty results), mark the source failing for re-check via the existing `mark_source_failing`, and never count it as "no scholarships found" — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T198, T192 — Satisfies: FR-HEALTH-2/5, SC-002
- [ ] T203 [US3] Register the health router ahead of the sources router — `backend/app/main.py` (EXTENDED) — Depends: T201, T194
- [ ] T204 [US3] Source recovery (O-2 resolved): make the workflow's source selection include `failing` sources (still excluding `disabled`) so a flagged source is re-checked on the normal schedule, and on a confirmed successful fetch clear its failing status by restoring `status=active` via the new `reactivate_source` helper — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) and `backend/app/data/repositories/source_repo.py` (EXTENDED, new `reactivate_source` helper) — Depends: T202, T200, T198 — Satisfies: FR-HEALTH-3/4, FR-MON-1, O-2

**Checkpoint**: P1 stories complete — monitoring, alerting, health.

---

## Phase 7: User Story 4 — Coverage analytics across the registry (P2)

**Goal**: a measured coverage snapshot with candidates counted separately and an explicit "never complete" flag.
**Independent test**: seed a known mix of active/failing/candidate sources over ≥2 countries; `GET /coverage` counts match exactly, `claims_complete_coverage=false`, gaps listed (quickstart US4).

### Tests first

- [ ] T205 [US4] Extend the coverage service tests: `candidate_sources_pending` counted separately from active (FR-COVAN-3); `verified_opportunities_count`; `sources_checked`/`sources_failed` derive from the latest fetch-log row per source; `last_checked_at` per source reflects the most recent run, computed live per request (FR-COVAN-4); gaps list a country/source-type with no healthy active source; `claims_complete_coverage` is always false; existing 001 callers/tests of `compute_coverage_summary` still pass unchanged — `backend/tests/services/test_coverage.py` (EXTENDED) — Satisfies: FR-COVAN-1..4, US4 Scenarios 1–5, SC-003
- [ ] T206 [P] [US4] Write failing API tests for `GET /coverage` returning the contract's `CoverageSnapshot` shape with arithmetic matching seed data and `claims_complete_coverage: false` — `backend/tests/api/test_coverage.py` (NEW) — Satisfies: contracts `/coverage`, FR-COVAN-2

### Implementation

- [ ] T207 [US4] Add only the missing fields (inspect first) to the existing `CoverageSummary`: `candidate_sources_pending`, `verified_opportunities_count`, per-source `last_checked_at` map, `gaps`; keep `claims_complete_coverage: Literal[False]` — `backend/app/schemas/discovery.py` (EXTENDED) — Depends: T205
- [ ] T208 [P] [US4] Create the thin response model `CoverageSnapshot` for the route, reusing `CoverageSummary` — `backend/app/schemas/coverage.py` (NEW) — Depends: T207
- [ ] T209 [US4] Extend `compute_coverage_summary` with the pending-candidate count and verified-opportunity count and the per-source last-checked map; function signature and 001 behavior unchanged — `backend/app/services/coverage.py` (EXTENDED) — Depends: T205, T207 — Satisfies: FR-COVAN-1/3/4
- [ ] T210 [US4] Create the thin coverage router calling the service — `backend/app/api/coverage.py` (NEW) — Depends: T206, T208, T209
- [ ] T211 [US4] Register the coverage router — `backend/app/main.py` (EXTENDED) — Depends: T210, T203

---

## Phase 8: User Story 5 — Distinguish a scholarship's lifecycle state at a glance (P2)

**Goal**: every record carries exactly one of the nine states, driven deterministically by monitoring outcomes. (Core transition rules already exist from Foundational; this phase wires the remaining transitions and proves the full walk.)
**Independent test**: walk one seeded record through discovered → verified → updated → stale → source_unavailable → closed → reopened, asserting exactly one `lifecycle_status` at each step via the 001 `GET /scholarships/{id}` endpoint (quickstart US5).
**Note**: wiring tasks here extend `source_monitor/graph.py`, so they run after US1's graph exists; the rules and the walk test are otherwise independent.

### Tests first

- [ ] T212 [P] [US5] Write the failing end-to-end walk test per quickstart US5 using a controllable clock and fixture fetches; assert exactly one of the nine values at every step; `source_unavailable` is never reported as `expired`/`closed`; `unverified`→`verified` on confirmed re-check; `closed`→`reopened` never `newly_discovered`; a fetch failure never changes a record to `closed`/`expired` — `backend/tests/integration/test_lifecycle_walk.py` (NEW) — Satisfies: FR-LIFECYCLE-1..5, US5 Scenarios 1–5, SC-004

### Implementation

- [ ] T213 [US5] Wire `source_unavailable`: when a source's retries are exhausted, apply `evaluate_fetch_outcome` to that source's scholarships (never `closed`/`expired`); wire the confirmed O-3 exit transitions on a later successful re-check (`stale`/`source_unavailable` → `verified` when details are confirmed, `unverified` when not fully confirmed) — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T212, T202 — Satisfies: FR-LIFECYCLE-3, US5 Scenario 3
- [ ] T214 [US5] Wire `unverified`→`verified` when a monitoring fetch confirms the record against its official source, and route every lifecycle write through one helper that no-ops when the evaluator returns `None` — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T213 — Satisfies: US5 Scenario 4, FR-LIFECYCLE-5
- [ ] T215 [US5] Make `u5_test_walk` pass end to end — `backend/tests/integration/test_lifecycle_walk.py` (EXTENDED) — Depends: T214

---

## Phase 9: User Story 8 — The deployed product is production-hardened (P2)

**Goal**: HTTPS, rate limiting, retry/backoff, structured logs + metrics, backups, persistent documents, fetch-log retention, and content/secret/dependency hygiene.
**Independent test**: quickstart US8 steps 1–8.
**Note**: tasks touching `main.py`, `graph.py`, `scheduler.py` extend files edited by earlier stories and therefore run after them.

### Tests first

- [ ] T216 [P] [US8] Write failing tests: requests beyond `RATE_LIMIT_PER_MINUTE` from one client IP get `429` with a clear JSON body and `Retry-After`; a second client IP is unaffected; the bucket refills — `backend/tests/core/test_rate_limit.py` (NEW) — Satisfies: NFR-HARDEN-2, US8 Scenario 2, SC-007
- [ ] T217 [P] [US8] Write failing tests that log records render as single-line JSON with timestamp, level, logger, message and extra fields (`run_id`, `source_id`, `request_id` when present) — `backend/tests/core/test_logging.py` (NEW) — Satisfies: NFR-HARDEN-4
- [ ] T218 [P] [US8] Write failing tests that `GET /metrics` returns Prometheus text exposition and that a monitoring run increments a run counter and a per-status fetch counter; **`GET /metrics` without a bearer token returns 401 and with a valid token returns 200 (O-4)** — `backend/tests/core/test_metrics.py` (NEW) — Satisfies: NFR-HARDEN-4, contracts `/metrics`, US8 Scenario 4
- [ ] T219 [P] [US8] Write failing tests for retention: rows older than `FETCH_LOG_RETENTION_DAYS` are pruned **except** the most recent `HEALTH_WINDOW_N` rows per source; a source with fewer than `HEALTH_WINDOW_N` rows keeps all of them even if old; `monitoring_runs`, `alerts`, `alert_preferences`, `candidate_source_validations` are never pruned — `backend/tests/services/test_retention.py` (NEW) — Satisfies: NFR-HARDEN-7, SC-009, Edge Case "retention vs open health flag"
- [ ] T220 [P] [US8] Write failing static tests that parse `docker-compose.podman.yml` and `Caddyfile`: a `caddy` service exists; the Caddyfile redirects HTTP→HTTPS and reverse-proxies to the backend; `/metrics` is not publicly exposed (Caddy block, in addition to app-level bearer auth — O-4); the backend service mounts a persistent volume at the `STORAGE_ROOT` path; no secret literals present — `backend/tests/infra/test_deploy_config.py` (NEW; create `backend/tests/infra/__init__.py`) — Satisfies: NFR-HARDEN-1/6, SC-007/008
- [ ] T221 [P] [US8] Write tests that a fixture page containing `<script>`, inline event handlers and `javascript:` links fetched through the monitoring path never reaches stored scholarship text or API output raw — `backend/tests/sources/test_monitor_sanitization.py` (NEW) — Satisfies: NFR-HARDEN-8, SC-010
- [ ] T222 [P] [US8] Write a test that walks the repository (pathlib, no git) and fails on obvious secret patterns (API keys, `password=` literals, private keys) outside `.env.example` placeholders and test fixtures marked allowed — `backend/tests/test_no_secrets_in_repo.py` (NEW) — Satisfies: NFR-HARDEN-8, SC-010
- [ ] T223 [P] [US8] Extend retry tests: a fetch that fails twice then succeeds yields exactly one `source_fetch_log` row with `retry_count=2` and `status=ok`; exhausted retries yield one `fail` row and the source marked failing — `backend/tests/sources/test_retry_policy.py` (EXTENDED) — Satisfies: NFR-HARDEN-3, US8 Scenario 3
- [ ] T224 [P] [US8] Extend the documents API tests: when the storage backend raises on write, the upload returns a visible error (non-2xx with a retry-able message) and no document row claims success — `backend/tests/api/test_documents_api.py` (EXTENDED) — Satisfies: Edge Case "persistent storage unavailable during upload"

### Implementation

- [ ] T225 [US8] Implement the in-memory per-IP token-bucket Starlette middleware — `backend/app/core/rate_limit.py` (NEW) — Depends: T216, T144
- [ ] T226 [P] [US8] Implement the structured JSON log formatter and a `configure_logging()` entry point — `backend/app/core/logging.py` (NEW) — Depends: T217
- [ ] T227 [P] [US8] Implement the `prometheus-client` registry, the run/fetch counters, and the `/metrics` route glue, protected by the existing authentication dependency so an unauthenticated request gets 401 (O-4) — `backend/app/core/metrics.py` (NEW) — Depends: T218, T141
- [ ] T228 [P] [US8] Implement the retention job per the rule above (single SQL path, returns deleted count) — `backend/app/services/retention.py` (NEW) — Depends: T219, T144
- [ ] T229 [US8] Emit a structured log event and increment metrics at run start/end and per source outcome inside the workflow — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T226, T227, T214
- [ ] T230 [US8] Add a daily retention job to the scheduler — `backend/app/scheduling/scheduler.py` (EXTENDED) — Depends: T228, T182
- [ ] T231 [US8] Mount the rate-limit middleware, call `configure_logging()`, and expose `/metrics` behind the existing bearer-auth dependency (O-4: authentication required, not just Caddy) — `backend/app/main.py` (EXTENDED) — Depends: T225, T226, T227, T211
- [ ] T232 [P] [US8] Create the TLS-terminating reverse-proxy config: automatic HTTPS, HTTP→HTTPS redirect, proxy to the backend, block `/metrics` from public ingress (defense in depth; the app itself also requires bearer auth — O-4) — `Caddyfile` (NEW, repo root) — Depends: T220
- [ ] T233 [US8] Add the `caddy` sidecar service and a named persistent volume mounted at `STORAGE_ROOT` for the backend — `docker-compose.podman.yml` (EXTENDED) — Depends: T232 — Satisfies: NFR-HARDEN-1/6
- [ ] T234 [P] [US8] Add a `pip-audit` step (fails on known critical findings) alongside the existing test and eval-gate steps — `.github/workflows/ci.yml` (EXTENDED) — Depends: T141 — Satisfies: NFR-HARDEN-8, Principle V
- [ ] T235 [US8] Make the upload path surface storage failures if `u8_test_upload` fails (no change if it already passes — record that in the commit notes) — `backend/app/api/documents.py` (EXTENDED) — Depends: T224
- [ ] T236 [US8] Make the sanitization test pass; add sanitization at the monitoring ingest boundary only if a gap is found (no change if none) — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T221, T229
- [ ] T237 [P] [US8] Document the fetch-log retention policy and its rationale, the Neon point-in-time-recovery backup check and a restore drill, HTTPS/Caddy deployment, rate-limit tuning, secret handling, and the persistent-volume redeploy check — `docs/operations-hardening.md` (NEW) — Depends: T228, T233 — Satisfies: NFR-HARDEN-5/7, SC-009 (backup recovery is demonstrated by you per this doc; no app code)

---

## Phase 10: User Story 7 — Candidate sources get automated pre-checks before human approval (P3)

**Goal**: advisory reachability / extractability / official-signal results shown to the reviewer; human approval remains the only promotion path.
**Independent test**: surface a candidate; `GET /sources/candidates/{id}/validations` has a populated row; candidate has no `source_registry` row regardless of results; a failed-check candidate can still be approved via the unchanged 001 endpoint (quickstart US7).

### Tests first

- [ ] T238 [US7] Write failing service tests: reachability via the ungated low-level `app.tools.web_fetch.fetch_url` (candidates are not registry sources, so the `source_id`-gated `official_fetch` must NOT be used); extractability via the existing `extract_listing` tool; official-signal heuristics (domain patterns such as `.gov`, `.edu`, `.ac.*`, keyword/affiliation matches) stored as advisory JSON, never a pass/fail gate; re-running appends a new row and the latest by `checked_at` is "current"; an AST test that the module never calls `approve_candidate_source`, `reject_candidate_source`, `upsert_source_by_domain`, or writes `SourceRegistry`/`CandidateSource.status` — `backend/tests/services/test_candidate_validation.py` (NEW) — Satisfies: FR-CANDVAL-1..3, Edge Case "candidate characteristics change"
- [ ] T239 [P] [US7] Write failing API tests: history newest-first, 404 on unknown candidate; a candidate failing a check can still be approved through the existing approve endpoint (FR-CANDVAL-4); an all-pass candidate can be rejected; a candidate with all-pass results has no `source_registry` row until approved (SC-006) — `backend/tests/api/test_candidate_validations_api.py` (NEW) — Satisfies: FR-CANDVAL-2/3/4, US7 Scenarios 2–5
- [ ] T240 [US7] Extend the workflow tests: pending candidates with no validation or a stale one are validated during a run; a validation exception is logged and never fails the run or touches candidate status — `backend/tests/workflows/test_source_monitor.py` (EXTENDED) — Depends: T198 — Satisfies: FR-CANDVAL-1

### Implementation

- [ ] T241 [P] [US7] Create the `CandidateSourceValidation` response schema — `backend/app/schemas/candidate_validation.py` (NEW) — Depends: T152
- [ ] T242 [P] [US7] Create the append-only validation repository (`add`, `list_for_candidate` newest-first, `latest_for_candidate`) — `backend/app/data/repositories/candidate_validation_repo.py` (NEW) — Depends: T152
- [ ] T243 [US7] Implement the validation service composing `fetch_url` + `extract_listing` + a pure official-signal heuristic, writing only to `candidate_source_validations` — `backend/app/services/candidate_validation.py` (NEW) — Depends: T238, T242
- [ ] T244 [US7] Add a workflow step that validates pending candidates lacking a recent validation (this is the trigger; `source_repo.record_or_bump_candidate_source` stays unchanged) — `backend/app/workflows/source_monitor/graph.py` (EXTENDED) — Depends: T240, T243, T236
- [ ] T245 [US7] Add `GET /sources/candidates/{candidate_id}/validations` only; no existing route changes and no write route is added — `backend/app/api/sources.py` (EXTENDED) — Depends: T239, T241, T242

---

## Phase 11: Polish & Cross-Cutting

- [ ] T246 [P] Write a contract-drift test comparing `specs/002-source-monitoring-hardening/contracts/openapi.yaml` to the live `app.openapi()` for the new paths/schemas (required arrays, enums, field presence), mirroring the 001 T133/T136 approach; fix whichever side drifted (never edit 001's contract) — `backend/tests/api/test_openapi_contract_002.py` (NEW) — Depends: T245, T231
- [ ] T247 Run the full backend suite and the existing scored eval gate; confirm the 001 tests (matching matrix, discovery guardrails, coverage) still pass unchanged — `backend/tests/` (no file change) — Depends: T246
- [ ] T248 Execute `specs/002-source-monitoring-hardening/quickstart.md` US1–US8 against your migrated environment and record pass/fail per step, including the three honest open items you could not demonstrate — `docs/t-002-verification-log.md` (NEW) — Depends: T247 (you apply migrations first)

---

## Dependencies & Execution Order

- **Phase 1 → Phase 2 → everything.** Foundational blocks all stories; migrations (T154, T155) are generated there and applied by you before any DB-backed test passes.
- **US6 (Phase 3) → US1 (Phase 4)**: monitoring uses the corrected fetch target and the `bypass_cache` addition built on T164.
- **US1 → US2, US3, US5, US8-graph, US7-graph**: all extend `source_monitor/graph.py`, which US1 creates. Their service/API/test files are independent and can be built in parallel with US1.
- **US4 (coverage)** depends only on Foundational + the fetch-log data; it can run in parallel with US2/US3.
- **Order for shared files** (`main.py`): T183 → T194 → T203 → T211 → T231. **For `graph.py`**: U1 core → diff → stale → U2 → U3 → U5 → U8 → U7.
- **No tasks are blocked**; O-1..O-4 are resolved (see Decisions). T249 (appended id) sits in Phase 2 and must complete before T195; T250 (appended id) sits in Phase 5, after T195.

## Parallel opportunities

- Setup: T142, T143 together.
- Foundational tests: T145, T146, T147, T148 together; then the three new model files together.
- US6 tests: all five `u6_test_*` tasks together.
- US1: T171, T172, T173, then T174, T175, T176 together.
- US8: every `u8_test_*` task together; T226, T227, T228, T232, T234, T237 together once their tests exist.

## Implementation strategy

1. **MVP**: Setup → Foundational → US6 → US1. Stop and demo a monitoring run catching a change.
2. Add US2 and US3 (completing P1), demoing the alert loop and the health view.
3. Add US4, US5, US8 (P2), then US7 (P3).
4. Finish with the Polish phase and your quickstart verification run.
