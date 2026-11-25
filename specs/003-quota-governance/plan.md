# Implementation Plan: LLM Quota Governance & Cheap Monitoring

**Branch**: `003-quota-governance` | **Date**: 2026-10-25 (updated 2026-11-26, post-audit/post-010) | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-quota-governance/spec.md` (approved, defaults accepted)

## Summary

Monitoring alone is estimated to need about 48 LLM requests a day against an observed free-tier cap of 20 (ADR-0012). This plan makes one change at the single place every provider call passes through, and two changes in how monitoring decides to spend:

1. A **central budget gate** at `app/core/llm.py` (story 2): daily allowance, a reserve for students, a priority ladder for background work, and provider-signal-driven pauses; students get a `503` with `Retry-After` instead of a bare 500 when it is spent.
2. **Change detection and cadence** in the connector and monitor (stories 1 and 3): a fingerprint of the page's visible text so unchanged pages cost no request, and per-source due times so annual sources are not read 24 times a day.
3. **Honest recording** (story 4): provider failures become a distinct outcome in the fetch history and stop moving source health, which is exactly what happened to HEC on 2026-10-24.

Deterministic extraction (story 5) is feasible for HEC and removes its remaining LLM use; it is not feasible for EACEA yet. Observability (story 6) reads persisted counters so numbers survive restarts, and the scheduler logs its interval at startup.

**Migration: yes, one additive Alembic revision is needed** (see "Migration statement"). Nothing in this plan is implemented yet (updated 2026-11-26, post-audit/post-010): still true on 2026-11-26 (audit B-25).

## Technical Context

**Language/Version**: Python 3.12 (existing)
**Primary Dependencies**: FastAPI, SQLAlchemy 2, Alembic, APScheduler 3.x (`BackgroundScheduler`), `google-genai` 2.22.0, `beautifulsoup4` (already declared), `prometheus-client` (already used in `core/metrics.py`). **No new dependency.** `zoneinfo` is stdlib and was verified working locally and in the production container.
**Storage**: PostgreSQL on Neon (pooled endpoint). Four new tables (ledger, pause state, check state, pending-classification queue) and two small column additions, all additive. (updated 2026-11-26, post-audit/post-010): the application connects as `scholarship_app` (no DDL right); migrations run as the owner over the direct URL; the role script is re-run after them.
**Testing**: pytest with the existing per-test rollback fixture, fake provider and fake clock; no live provider calls in CI; one new scored eval case.
**Target Platform**: Linux container (podman) on the Oracle VM behind Caddy; single uvicorn worker today, with the scheduler in-process.
**Project Type**: Backend-only modular monolith (frontend deferred, M-67).
**Performance Goals**: The gate adds one short database transaction to each LLM call (negligible at about 20 calls a day). A scheduler tick that finds nothing due must do no network or LLM work.
**Constraints**: 20 requests/day for the generation model (configurable); counting must be exact across workers and restarts; no secrets or page content in logs or recorded reasons; monitoring stays a deterministic workflow, not an agent.
**Scale/Scope**: 2 monitored sources, 5 registered, 0 production users. The design must still work unchanged when the allowance becomes large (paid tier) or sources multiply (feature 006).

## What the real code does today (checked before designing)

| Area | Fact (file:line) | Consequence for the plan |
|---|---|---|
| LLM access | `core/llm.py:33,48`: `generate_text` and `embed_texts` call the client directly. No counter, retry, quota guard or priority. | Single choke point for the gate (R-01). |
| Provider error handling | Only `workflows/doc_pipeline/graph.py:143` catches `genai_errors.APIError` / `LlmNotConfiguredError`. Everywhere else a provider error escapes. | New `LlmError` base; update that one handler; student routes get the 503 mapping (R-03). |
| Extraction call | `sources/connectors/official_fetch.py:420` `extract_listing(...)` runs on every successful fetch; candidate-link detection runs just before it (line 418). | Fingerprint check goes between the redirect checks (~416) and line 418 (R-07). |
| Monitoring bypasses cache | `workflows/source_monitor/graph.py:447` `bypass_cache=True`. | The TTL cache never helped monitoring; fingerprint replaces it for that path. |
| Provider error becomes a health failure | `source_monitor/graph.py:397-416` `_record_crashed_check` writes a `FAIL` row, `touch_source_check(success=False)` and a health failure for any exception. | This is the 2026-10-24 HEC incident (FR-ERR-2). Provider errors are caught earlier and recorded as `provider_*`. |
| Source selection | `source_monitor/graph.py:569` takes every active/failing web discovery source on every run. | Add due-selection (R-09). |
| Scheduler | `scheduling/scheduler.py:59` builds an interval job; `start_scheduler` (line 80) logs nothing about the interval. | Startup log line (FR-OBS-2). |
| Fetch log schema | `source_fetch_log.status` is `VARCHAR(7)`, no CHECK; columns `id, source_id, started_at, status, http_status, error, retry_count, items_found, fetched_url, used_homepage_fallback, monitoring_run_id`. | Add a nullable `outcome` column instead of new status values (R-10). |
| Fetch log readers | Failure streak `data/repositories/source_repo.py:338` (`status != OK`, shared by health and retention); coverage `services/coverage.py:63-75`; baseline lookups `official_fetch.py:259`, `source_monitor/graph.py:171`, `services/retention.py:72` (all need `items_found IS NOT NULL`). | Two readers must ignore `provider_*` rows; the others are already safe for the new success-type rows. |
| Run counters | `monitoring_runs` has `sources_processed`, `sources_failed`, `changes_detected` only. | Add skipped and deferred (FR-OBS-3). |
| Registry write path | Only `upsert_source_by_domain` (`source_repo.py:169`), a full replace except `listing_page_url` and `freshness_window_days`. | Re-seeding production would reset EACEA `failing` to `active`. A dedicated rules/cadence command is added; production is not re-seeded (R-11). |
| Parsing | `beautifulsoup4` is a declared dependency. | Rule engine needs no new dependency. |

## What changed since 2026-10-25 (updated 2026-11-26, post-audit/post-010)

Feature 010 (deployed 2026-11-20) and the 2026-10-26 audit changed the ground this plan stands on. Each row says what changed, why it matters here, and what this plan now does. Line numbers in the table above were read on 2026-10-25 and have moved; every task re-reads the file before editing it.

| Change | Reason it matters | What the plan now does |
|---|---|---|
| Alembic head is `a807daf8460e` (010 security hardening), not `f2b8d4a6c1e3` | A revision with the old `down_revision` would create a second head and break CI and deploy | New revision uses `down_revision = a807daf8460e`; its test reads the head with `alembic heads` first (T254) |
| The application and CI tests run as `scholarship_app`; migrations run as the owner; the role script is re-run after migrations (`.github/workflows/ci.yml`, runbook section 14) | The three new tables must be usable by the app without a manual grant, and the app has no DDL right | No grants in the revision; the role script's default privileges (`FOR ROLE <owner>`, SELECT/INSERT/UPDATE/DELETE) cover them; the role script is re-run after the migration (deploy task and the CI order are unchanged); a test creates and reads each new table as `scholarship_app` |
| `web_fetch` rewritten: `safe_get` checks every hop against the public-address rule and pins the connection; `fetch_url` keeps its keyword signature and returns `FetchResult` with `blocked` and `final_url` | Conditional request headers must not weaken or bypass the per-hop check, and must not be sent to a redirect target | Conditional headers are an optional argument of `safe_get`/`fetch_url`, sent on the first request only (like `params`), never forwarded across a redirect; the one-argument `fetch` seam used by `fetch_official` keeps working; a blocked fetch is a site-side failure and is never a provider outcome |
| `require_operator` exists (`api/deps.py`); `POST /monitoring/runs`, `GET /monitoring/runs/{id}` and `GET /sources/{id}/fetch-log` are operator-only; `GET /monitoring/runs` and `GET /sources/health` stay open to any signed-in user | The old risk row "anyone logged in can trigger a manual monitoring run" is no longer true; the new budget view needs the same gate (FR-OBS-5) | Risk row replaced; the budget and cadence view is a new operator-only read route (decision 9); the new run counters and health fields keep the access level of the route that carries them |
| `POST /monitoring/runs` accepts no source list: "a run always covers the governed set `run_source_monitor` loads itself" (`api/monitoring.py` docstring, a 002 decision) | Story 3 scenario 6 and T296 say a manual run "may name sources"; no request body changes in api-delta | Decided 2026-11-26: the manual run checks all due sources and obeys the budget; the 002 route and its body stay unchanged; scenario 6 no longer says "or those the operator names" (updated 2026-11-26, manager decisions) |
| Audit found: ingestion's own log rows defeat the cross-run extraction cache (P4-OF-3) | Discovery re-extracts every source on every click, one LLM call each | New FR-SKIP-8, research R-15, tasks T452-T453 |
| Audit found: every LLM route except documents answers a provider failure with HTTP 500 (B-57, section 7) | Students get a server error instead of "try again later" | FR-ERR-6 widens the 503 mapping from "allowance spent or paused" to any provider failure (research R-16, tasks T454-T456) |
| Audit found: classify makes about one LLM call per new candidate and swallows LLM failures (T-05, BR-02) | The quota projection below undercounted spend; a gated classify call can be refused | Projection corrected; classify is gated and counted; a refused or failed call stores the record as today and queues it, and a backfill-class drain fills only unknown dimensions later (FR-BUDGET-13, research R-17, R-20) |
| Model names are constants (B-55) | A paid-tier or model change at 006 should not need a code edit | FR-BUDGET-12: model names from settings, defaults unchanged, tasks T457-T458 |
| Scheduler tick now rolls back on a database error and 010 added email alert delivery to the run (010 FR-ROLLBACK, FR-MAIL) | The cheap tick must not skip the rollback or the alert step | T300 keeps both; its test injects a database error |
| `main.py` now adds `BodyLimitMiddleware` and `RateLimitMiddleware` and refuses to start on weak secrets or owner-level credentials | The `user_facing` middleware must sit inside them, and the gate is installed after the start-up checks | T273 states the order and tests it |
| ADR-0015 and ADR-0016 exist; Blueprint v1.9 and PRD v1.3 are in force | Build order is now 010, 003, 004, 006, 005, 011, 007, 008/009; ADR-0012's order is amended by ADR-0015 | References updated; no requirement ID changed |

## Constitution Check

*GATE: passed before research; re-checked after design.*

| Principle | Assessment |
|---|---|
| I. No fabrication | Pass. Rule results use the same grounding gate (threshold 1.0) as LLM results; names are read verbatim; unknown fields stay unknown (no country, field or funding guessed from HEC card categories). A skipped or deferred check changes no record. |
| II. Deterministic hard-constraint matching | Pass. The matching path is untouched. When the budget refuses a soft-preference LLM call, a student gets a temporary-unavailable response and an alert re-match pair is skipped; neither produces a verdict with a guessed soft result. |
| III. Human approval before live submission | Not touched. No send path is added or altered. |
| IV. Registry-approved sources only | Pass. Fetching stays gated by the registry. Extraction rules are human-defined configuration on the registry row, never authored or activated by the LLM (FR-RULES-5). Provider failures are recorded and are never read as "no scholarship found", extending the existing rule to LLM-side failures. |
| V. Spec-driven, tests and evals gated in CI | Pass. Spec approved; this plan; tasks next. Tests per research R-13, plus one new scored eval (`quota_governance`, threshold 1.0) added to the CI score gate. |
| Architecture constraints | Pass. No sixth agent, no stack change, no new dependency. The gate is a service plus a thin core interface, consistent with the existing layering. |

**Post-design re-check**: unchanged. One architectural observation: the central LLM gate and the `outcome` model are cross-cutting and have lasting consequences, so they meet the ADR test (see "ADR suggestion").

## Design by story

**Story 2, budget (P1).**
- `app/core/llm_gate.py` (new): `Priority` {`user_facing`, `backfill`, `monitoring`, `validation`}, `RequestType` {`generate`, `embed`}, a context-variable and `llm_priority(...)` context manager, exceptions `LlmError`, `LlmPriorityMissing`, `LlmBudgetDeferred(retry_at)`, `LlmTemporarilyUnavailable(retry_at, reason)`, `LlmQuotaExhausted`, `LlmProviderError`, `LlmResponseUnparseable`, and a gate registry. No database imports.
- `app/services/llm_budget.py` + `data/repositories/llm_budget_repo.py` + `models/llm_budget.py` (new): the database-backed gate. Admission order: pause check, then allowance, then ladder floor, then backfill share; one atomic reservation under an advisory transaction lock on its own short session. Policy values in research R-02.
- `core/llm.py`: acquire before each provider call, translate `APIError` after it, report 429 pauses (daily-quota detection per R-03).
- `main.py`: install the gate in lifespan (after 010's start-up checks); middleware sets `user_facing` and sits inside 010's body-limit and rate-limit middleware; one exception handler maps `LlmTemporarilyUnavailable` and, (updated 2026-11-26, post-audit/post-010), every other `LlmError` and `LlmNotConfiguredError` to the 503 body in `contracts/api-delta.md` (FR-ERR-6, research R-16).
- Entry-point scopes: monitor run and alert re-match = `monitoring`; candidate validation = `validation`; backfill helper for feature 004 = `backfill`.
- `doc_pipeline/graph.py:143`: catch `LlmError` so the upload fix keeps returning 201 with `extraction_unavailable`.

**Story 1, unchanged pages (P1).** `services/content_fingerprint.py` (normalisation reuses `clean_html_to_text`), `data/repositories/check_state_repo.py`, `models/check_state.py`; skip logic in `fetch_and_extract_listing`; new `unchanged` result handled in `_monitor_one_source` (own branch: success touch and healthy transition, no change detection, no `mark_seen_verified`, no stale sweep) and in `listing_fetch_tool` (mapped to `cached` for discovery). Conditional requests: `safe_get`/`fetch_url` gain optional request headers (first request only, never forwarded across a redirect) and 304 handling; used only when validators are trusted (updated 2026-11-26, post-audit/post-010). **Discovery cache fix (FR-SKIP-8, research R-15)**: `_last_successful_extraction` stops treating ingestion's per-record success rows as extraction rows. Forced full re-check every `min(7 days, freshness window / 2)`. Back-off and history bootstrap per R-08.

**Story 3, cadence (P2).** `services/cadence.py` (pure functions: interval derivation, due selection, stalest-first order, back-off). `run_source_monitor` filters to due sources; a scheduled tick with nothing due and no candidate to validate returns without creating a run row (decision 5). **Every tick, including an idle one, logs exactly one structured line** with the number of sources due, checked, skipped as unchanged and deferred, so the scheduler is provably alive even when no run row is written. Manual runs obey the budget but may name sources and always create a run row. `monitoring_runs` gains skipped and deferred counters.

**Story 4, provider failures (P2).** In the connector, wrap the extraction call: quota gives a `deferred`/`provider_quota` outcome and a `next_attempt_at`, other provider errors give `provider_error` or `provider_parse`; none calls `mark_source_failing`, touches health, or modifies a record. `_record_crashed_check` treats an `LlmError` as a provider outcome, not a site failure. Discovery uses the same connector function, so it is covered by the same change (the agent's top-level catch from the earlier logging fix remains a last resort). Candidate validation records the provider outcome on its advisory result. Health API and `ranked_monitoring_runs` / coverage readers updated per `data-model.md`.

**Deferred classification (FR-BUDGET-13) (updated 2026-11-26, manager decisions).** `classify` reports a refused or failed LLM step; ingestion stores the record as today and writes a row in `classify_pending` (new table, `models/classify_pending.py`, `data/repositories/classify_pending_repo.py`); a scheduler step drains it oldest first under the backfill class and fills only still-unknown dimensions (`services/classify_backfill.py`); the queue size appears in the operator view and as a gauge. Research R-20.

**Story 5, deterministic rules (P3).** `tools/rule_extract.py` (declarative engine over `bs4`), a rule schema validated in `schemas/source.py`, rules stored under `extraction_rules['deterministic']`. Order inside the connector: rules, then (only if rules match nothing, or none configured) the LLM through the gate. Anomaly guard: new count below 50% or above 200% of the previous count raises `rules_review_needed` and holds the result for the operator. A command validates and sets only the rule and cadence keys on one source, and writes a 010 audit row (`source.rules.set`; `--operator-email` must be an existing operator; FR-RULES-6) (updated 2026-11-26, manager decisions). HEC rules are also added to `seed_sources.yaml`. Feasibility and caveats: research R-11.

**HEC enablement is gated by a parity check (decision 7).** No HEC category is excluded. Before rules are enabled for HEC: (1) save the real listing page once as a committed fixture; (2) record the LLM's extraction of that exact saved page once as a committed snapshot (one owner-approved request, run under the `one-off backfill` class; tests never call the provider); (3) run the rules over the same saved page; (4) the check passes only if every item in the LLM snapshot is also found by the rules (compared by normalised name), and it prints three lists: `matched`, `LLM-only` (these block enabling), and `rules-only` (flagged for the owner, not blocking); (5) the set-rules command refuses to enable the HEC rule set unless that check passes against the committed fixtures, and the report is shown to the owner before the rule set is switched on in production. A read-only comparison against the stored scholarships is a second, informational report.

**Story 6, visibility (P2).** Scrape-time collector reading the ledger and pause rows; (updated 2026-11-26, post-audit/post-010) an operator-only read route `GET /monitoring/budget` (approved name; `require_operator`; FR-OBS-5; research R-18); per-source noise flag; one structured startup log line from `start_scheduler`; extended run-finished log line. Series in `contracts/api-delta.md`.

## Migration statement

**Yes. One additive Alembic revision is needed**, with `down_revision = a807daf8460e` (the current head, 010's security-hardening revision; was `f2b8d4a6c1e3` on 2026-10-25) (updated 2026-11-26, post-audit/post-010). Contents:

1. Create `llm_budget_ledger` and `llm_budget_state`, and `classify_pending` (updated 2026-11-26, manager decisions).
2. Create `source_check_state` (foreign key to `source_registry.id`).
3. Add nullable `source_fetch_log.outcome VARCHAR(24)`.
4. Add `monitoring_runs.sources_skipped` and `monitoring_runs.sources_deferred`, `INTEGER NOT NULL DEFAULT 0`.

Properties for you as the person applying it:
- **Additive only**: no column is altered or dropped, no data is rewritten, no CHECK constraint is added; the two integer columns use a constant default, which is metadata-only on current PostgreSQL, and the nullable column needs no rewrite.
- **Backward compatible**: the code running in production today ignores the new tables and columns, so the safe order is migrate first, then deploy, with no downtime and no coordinated cutover.
- **Privileges** (updated 2026-11-26, post-audit/post-010): the revision grants nothing. It runs as the owner; the role script's default privileges (`FOR ROLE <owner>`) make the new tables usable by `scholarship_app`, and the role script is re-run after the migration (runbook section 14, same as CI). `audit_log` and `alembic_version` rules are untouched.
- **Rollback**: redeploying the previous code is safe at any time; `alembic downgrade -1` drops the new objects and loses only budget history, check state and the `outcome` annotations.
- **No data migration**; the fetch-history bootstrap for back-off (research R-08) is done lazily in code when a state row is first created.
- Table details: `data-model.md`. I will write the revision file in the tasks/implementation phase; I will not apply it.

No migration is needed for the interval override or the rule set (stored in the existing `extraction_rules` JSON) or for any setting.

## Quota projection (estimates)

| Day type | Today | After 003 |
|---|---|---|
| Quiet day, steady state | ~48 (E) | ~0.3-0.7 (E): HEC 0 (unchanged, and rules cover the weekly forced check); EACEA about 1 per week at the capped back-off (about 0.14 a day); candidate validation about 0.3 (2 pending, 7-day recheck) |
| First day after deploy | ~48 (E) | at most 3 (E): one first check per source (HEC 0 if rules are enabled), EACEA bootstrapped to the capped back-off |
| A day when HEC's page changes | ~48 (E) | HEC 0 with rules; 1 without rules (E) |

Against the approved success criteria: SC-001 asks for at most 4 on a quiet day (met with margin); SC-007 asks for the reserve of 8 to stay available (guaranteed by the ladder).

(updated 2026-11-26, post-audit/post-010) **Correction.** The table above counts extraction calls only. Every new candidate also costs about one `classify` call (T-05, BR-02: measured under mocks as 1 per candidate, not on real pages). So a day on which a monitored page gains k scholarships costs k extra requests (HEC with rules: 0 + k; EACEA or HEC without rules: 1 + k). On a quiet day (no new candidates) the table stands. On a busy day the monitoring class is admitted only while used requests stay below 10 of 20 (the ladder), so the largest day's classify load is bounded and the rest is deferred and counted, but a deferred classify call is queued and retried by the backfill-class drain (FR-BUDGET-13, R-20) (updated 2026-11-26, manager decisions). SC-014 makes this visible after deploy (background requests per day beside new candidates per day) instead of assuming it. The pre-003 estimate of about 48 a day is for the 60-minute tick; on today's 360-minute stopgap it is about 8 (E).

## Project Structure

### Documentation (this feature)

```text
specs/003-quota-governance/
├── spec.md              # approved
├── plan.md              # this file
├── research.md          # Phase 0 decisions and evidence (R-01 .. R-14)
├── data-model.md        # tables, columns, state transitions
└── contracts/
    └── api-delta.md     # changed response shapes and metrics series
```

No `tasks.md` (next step, `/sp.tasks`). No `quickstart.md`: nothing here needs an operator walk-through beyond the migration and rollout notes in this plan. I did not run the agent-context update script (it rewrites `CLAUDE.md`, which you did not ask me to touch).

### Source code (additive unless marked)

```text
backend/app/
├── core/
│   ├── llm.py                      # modified: acquire/translate/report
│   ├── llm_gate.py                 # new: priority, exceptions, context, registry
│   ├── config.py                   # modified: budget, cadence, rules settings, model names (updated 2026-11-26, post-audit/post-010)
│   └── metrics.py                  # modified: budget collector, noise gauge
├── models/
│   ├── llm_budget.py               # new: ledger, state
│   ├── check_state.py              # new: source_check_state
│   ├── classify_pending.py         # new: pending-classification queue
│   ├── source.py                   # modified: SourceFetchLog.outcome
│   └── monitoring.py               # modified: skipped/deferred columns
├── data/repositories/
│   ├── llm_budget_repo.py          # new
│   ├── check_state_repo.py         # new
│   ├── classify_pending_repo.py    # new
│   ├── audit_repo.py               # modified: action source.rules.set
│   └── source_repo.py              # modified: streak query, log_fetch outcome, rules/cadence setter
├── services/
│   ├── classify_backfill.py        # new: drain of the pending-classification queue (backfill class)
│   ├── llm_budget.py               # new: DB-backed gate, day boundary, error parsing, budget-view read
│   ├── content_fingerprint.py      # new
│   ├── cadence.py                  # new
│   ├── coverage.py                 # modified: ignore provider_* in failed counts
│   ├── retention.py                # modified: prune old ledger rows
│   └── candidate_validation.py     # modified: class + provider outcome
├── tools/
│   ├── web_fetch.py                # modified: optional headers, 304
│   ├── rule_extract.py             # new: declarative engine
│   └── listing_fetch.py            # modified: unchanged/deferred mapping
├── sources/connectors/official_fetch.py   # modified: fingerprint, rules, provider handling
├── workflows/
│   ├── source_monitor/graph.py     # modified: due selection, unchanged/deferred branches, class scope
│   └── doc_pipeline/graph.py       # modified: catch LlmError
├── agents/discovery/agent.py       # modified: handle new statuses
├── scheduling/scheduler.py         # modified: cheap tick, startup log
├── schemas/{source,monitoring,health}.py   # modified
├── api/{health,monitoring}.py      # modified; monitoring.py gains the operator-only budget view (updated 2026-11-26, post-audit/post-010)
├── data/seeds/seed_sources.yaml    # modified: HEC rule set (disabled until parity passes)
└── main.py                         # modified: install gate, middleware, 503 handler
backend/migrations/versions/<rev>_quota_governance.py   # new (written in implementation)
backend/evals/cases/quota_governance.py                  # new scored case
backend/tests/{core,services,tools,workflows,sources,api,evals}/...   # new tests per R-13
backend/tests/fixtures/hec/{listing.html,llm_snapshot.json}          # recorded once; parity check input
```

**Structure decision**: reuse the existing modular-monolith layout; the only new top-level concept is the gate interface in `core`, kept free of database imports so the layering stays acyclic.

## Rollout

1. Merge the slices below to `main` one at a time (each green in CI). Suggested slices, which `/sp.tasks` will refine: (1) schema, gate, priority scopes, 503 handler (story 2); (2) provider outcomes and health exclusions (story 4); (3) fingerprint and unchanged path (story 1); (4) cadence and cheap tick (story 3); (5) rule engine, HEC rules, parity check and set-rules commands (story 5); (6) metrics, startup log, eval case, runbook update (story 6).
2. **You apply the migration** before the first deploy that needs it (updated 2026-11-26, post-audit/post-010): owner-run, as the owner role over the direct URL, then re-run the role script, then recreate the containers in the runbook section 10 order, following the pattern of runbook section 15 (new runbook section 16, tasks T463-T468).
3. Deploy, verify `/health`, check the startup log line shows the interval and budget settings.
4. Change `MONITORING_INTERVAL_MINUTES` from 360 back to 60 (or leave it; cost no longer depends on it). (updated 2026-11-26, post-audit/post-010): owner decision after the first week of measurements (T326); until then 360 stays.
5. Record the HEC fixture and LLM snapshot (one request), run the parity check, send the owner the report (matched / LLM-only / rules-only), and only if it passes and the owner agrees enable the HEC rule set with the set-rules command (never by re-seeding production).
6. Measure for 7 days against SC-001.

## Risks

| Risk | Mitigation |
|---|---|
| Fingerprint hides a link-only change | Weekly forced full re-check; limitation recorded (R-06) |
| Priority missed on a new LLM path | Fail closed with a loud refusal; middleware default for HTTP; a test enumerates the known entry points |
| Gate adds latency or fails on a Neon cold start | One short transaction; user-facing calls fail open to the provider's own limit; background defers |
| Daily-quota pause mis-detected as short rate limit | Real error shape captured as a test fixture (R-03) |
| Other consumers of the same key (laptop, smoke tests) | Provider signal is authoritative and visible as `discrepancy` |
| HEC rules find a different set from the LLM (38 unique names on the page vs 32 from the LLM) | Parity check on a saved page against a recorded LLM snapshot; enabling requires rules to find everything the LLM found; every difference is reported to the owner; no category is excluded (decision 7) |
| Re-running the seed on production resets source status | Dedicated set-rules command; documented in the runbook; production is not re-seeded |
| ~~Anyone logged in can trigger a manual monitoring run~~ (updated 2026-11-26, post-audit/post-010): closed by 010 (operator-only, audited) | New risks below |
| Classify calls consume the allowance and a refused call is silent in what it stores (T-05) (updated 2026-11-26, post-audit/post-010) | Gated and counted; the busiest day is bounded by the ladder; refused calls are queued and drained under backfill (FR-BUDGET-13); SC-014 measures it (updated 2026-11-26, manager decisions) |
| The new tables are not usable by the restricted role (updated 2026-11-26, post-audit/post-010) | Default privileges plus the role script re-run; a test as `scholarship_app` creates and reads each table; the deploy re-runs the role script before recreating containers |
| The discovery cache fix changes which rows count as an extraction (updated 2026-11-26, post-audit/post-010) | Test starts at `POST /discovery` with ingestion rows present; retention floor (c) is checked for the same confusion (T453) |

## Decisions (reviewed and settled 2026-10-25)

1. **Outcome model (R-10): accepted.** New nullable `outcome` column, `status` unchanged; provider failures stored as `fail` plus `outcome = provider_*` and excluded from health and coverage by two readers.
2. **Ladder offsets and backfill share (R-02): accepted.** Floors at remaining > 8 (backfill), > 10 (monitoring), > 12 (validation); backfill capped at 6 a day.
3. **Student-triggered discovery counts as user-facing: accepted.**
4. **Fail-open for students** if the budget database is unreachable (background defers): accepted.
5. **Quiet scheduled ticks create no `monitoring_runs` row: accepted, with one addition.** Every tick logs one line with sources due / checked / skipped (and deferred) so the scheduler is provably alive. Manual runs always create a row.
6. **Temporary-unavailable = HTTP 503 with `Retry-After`** and a JSON body: accepted.
7. **HEC rollout: changed.** No category is excluded. Rules are enabled for HEC only after a parity check (rules find at least everything the LLM found, on one saved page with a recorded LLM snapshot, no live call in tests); all differences are flagged to the owner (see Story 5).
8. **Added 2026-11-26 (owner-approved, audit-driven)**: model names come from configuration with unchanged defaults and no model change (FR-BUDGET-12; ADR-0012 keeps that at 006).
9. **Operator-only budget and cadence view** through `require_operator`, route `GET /monitoring/budget` approved 2026-11-26 (FR-OBS-5).
10. **Any provider failure on an LLM route is the temporary-unavailable answer**, not only an exhausted allowance (FR-ERR-6); document upload keeps 201 `extraction_unavailable`.
11. **The extraction cache is fixed in 003** (FR-SKIP-8), by reading only the source's own listing-extraction rows.
12. **Classify is deferred, not dropped** (FR-BUDGET-13): refused or failed calls queue the record; a backfill-class drain fills only unknown dimensions (updated 2026-11-26, manager decisions).
13. **The rules `set` command is audited** (FR-RULES-6, D-1) and **ADR-0014 gets a dated amendment** for FR-ERR-6 (D-2) (updated 2026-11-26, manager decisions).

## Open questions for the manager (updated 2026-11-26, manager decisions)

None open. OQ-1 (classify under the gate) is decided: refused or failed classify calls are queued and drained under the backfill class (FR-BUDGET-13, research R-20). OQ-2 (audit of the rules commands) is decided: `set` writes a 010 audit row (FR-RULES-6, spec D-1). Both decided by the manager on 2026-11-26 (owner delegated).

## Architecture decision record

ADR-0014 (`adr/0014-llm-budget-gate-and-fetch-outcomes.md`, Accepted 2026-10-25; not edited here) records the central LLM budget gate, the reserve ladder, provider-signal pauses, the Postgres counter store, the 503 refusal, the `outcome` column with the provider-failure exclusion (which amends ADR-0008's failed-run definition), and the unchanged-page skip semantics. It uses the repo's `adr/` convention.

## Complexity Tracking

No constitution violations to justify. The one non-obvious cost is a second small database transaction per LLM call, accepted for exact, shared counting (research R-04).
