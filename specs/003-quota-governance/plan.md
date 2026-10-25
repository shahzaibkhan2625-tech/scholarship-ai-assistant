# Implementation Plan: LLM Quota Governance & Cheap Monitoring

**Branch**: `003-quota-governance` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-quota-governance/spec.md` (approved, defaults accepted)

## Summary

Monitoring alone is estimated to need about 48 LLM requests a day against an observed free-tier cap of 20 (ADR-0012). This plan makes one change at the single place every provider call passes through, and two changes in how monitoring decides to spend:

1. A **central budget gate** at `app/core/llm.py` (story 2): daily allowance, a reserve for students, a priority ladder for background work, and provider-signal-driven pauses; students get a `503` with `Retry-After` instead of a bare 500 when it is spent.
2. **Change detection and cadence** in the connector and monitor (stories 1 and 3): a fingerprint of the page's visible text so unchanged pages cost no request, and per-source due times so annual sources are not read 24 times a day.
3. **Honest recording** (story 4): provider failures become a distinct outcome in the fetch history and stop moving source health, which is exactly what happened to HEC on 2026-10-24.

Deterministic extraction (story 5) is feasible for HEC and removes its remaining LLM use; it is not feasible for EACEA yet. Observability (story 6) reads persisted counters so numbers survive restarts, and the scheduler logs its interval at startup.

**Migration: yes, one additive Alembic revision is needed** (see "Migration statement"). Nothing in this plan is implemented yet.

## Technical Context

**Language/Version**: Python 3.12 (existing)
**Primary Dependencies**: FastAPI, SQLAlchemy 2, Alembic, APScheduler 3.x (`BackgroundScheduler`), `google-genai` 2.22.0, `beautifulsoup4` (already declared), `prometheus-client` (already used in `core/metrics.py`). **No new dependency.** `zoneinfo` is stdlib and was verified working locally and in the production container.
**Storage**: PostgreSQL on Neon (pooled endpoint). Three new tables and two small column additions, all additive.
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
- `main.py`: install the gate in lifespan; middleware sets `user_facing`; one exception handler maps `LlmTemporarilyUnavailable` to the 503 body in `contracts/api-delta.md`.
- Entry-point scopes: monitor run and alert re-match = `monitoring`; candidate validation = `validation`; backfill helper for feature 004 = `backfill`.
- `doc_pipeline/graph.py:143`: catch `LlmError` so the upload fix keeps returning 201 with `extraction_unavailable`.

**Story 1, unchanged pages (P1).** `services/content_fingerprint.py` (normalisation reuses `clean_html_to_text`), `data/repositories/check_state_repo.py`, `models/check_state.py`; skip logic in `fetch_and_extract_listing`; new `unchanged` result handled in `_monitor_one_source` (own branch: success touch and healthy transition, no change detection, no `mark_seen_verified`, no stale sweep) and in `listing_fetch_tool` (mapped to `cached` for discovery). Conditional requests: `fetch_url` gains optional request headers and 304 handling; used only when validators are trusted. Forced full re-check every `min(7 days, freshness window / 2)`. Back-off and history bootstrap per R-08.

**Story 3, cadence (P2).** `services/cadence.py` (pure functions: interval derivation, due selection, stalest-first order, back-off). `run_source_monitor` filters to due sources; a scheduled tick with nothing due and no candidate to validate returns without creating a run row (decision 5). **Every tick, including an idle one, logs exactly one structured line** with the number of sources due, checked, skipped as unchanged and deferred, so the scheduler is provably alive even when no run row is written. Manual runs obey the budget but may name sources and always create a run row. `monitoring_runs` gains skipped and deferred counters.

**Story 4, provider failures (P2).** In the connector, wrap the extraction call: quota gives a `deferred`/`provider_quota` outcome and a `next_attempt_at`, other provider errors give `provider_error` or `provider_parse`; none calls `mark_source_failing`, touches health, or modifies a record. `_record_crashed_check` treats an `LlmError` as a provider outcome, not a site failure. Discovery uses the same connector function, so it is covered by the same change (the agent's top-level catch from the earlier logging fix remains a last resort). Candidate validation records the provider outcome on its advisory result. Health API and `ranked_monitoring_runs` / coverage readers updated per `data-model.md`.

**Story 5, deterministic rules (P3).** `tools/rule_extract.py` (declarative engine over `bs4`), a rule schema validated in `schemas/source.py`, rules stored under `extraction_rules['deterministic']`. Order inside the connector: rules, then (only if rules match nothing, or none configured) the LLM through the gate. Anomaly guard: new count below 50% or above 200% of the previous count raises `rules_review_needed` and holds the result for the operator. A command validates and sets only the rule and cadence keys on one source. HEC rules are also added to `seed_sources.yaml`. Feasibility and caveats: research R-11.

**HEC enablement is gated by a parity check (decision 7).** No HEC category is excluded. Before rules are enabled for HEC: (1) save the real listing page once as a committed fixture; (2) record the LLM's extraction of that exact saved page once as a committed snapshot (one owner-approved request, run under the `one-off backfill` class; tests never call the provider); (3) run the rules over the same saved page; (4) the check passes only if every item in the LLM snapshot is also found by the rules (compared by normalised name), and it prints three lists: `matched`, `LLM-only` (these block enabling), and `rules-only` (flagged for the owner, not blocking); (5) the set-rules command refuses to enable the HEC rule set unless that check passes against the committed fixtures, and the report is shown to the owner before the rule set is switched on in production. A read-only comparison against the stored scholarships is a second, informational report.

**Story 6, visibility (P2).** Scrape-time collector reading the ledger and pause rows; per-source noise flag; one structured startup log line from `start_scheduler`; extended run-finished log line. Series in `contracts/api-delta.md`.

## Migration statement

**Yes. One additive Alembic revision is needed**, with `down_revision = f2b8d4a6c1e3` (the current head). Contents:

1. Create `llm_budget_ledger` and `llm_budget_state`.
2. Create `source_check_state` (foreign key to `source_registry.id`).
3. Add nullable `source_fetch_log.outcome VARCHAR(24)`.
4. Add `monitoring_runs.sources_skipped` and `monitoring_runs.sources_deferred`, `INTEGER NOT NULL DEFAULT 0`.

Properties for you as the person applying it:
- **Additive only**: no column is altered or dropped, no data is rewritten, no CHECK constraint is added; the two integer columns use a constant default, which is metadata-only on current PostgreSQL, and the nullable column needs no rewrite.
- **Backward compatible**: the code running in production today ignores the new tables and columns, so the safe order is migrate first, then deploy, with no downtime and no coordinated cutover.
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
│   ├── config.py                   # modified: budget, cadence, rules settings
│   └── metrics.py                  # modified: budget collector, noise gauge
├── models/
│   ├── llm_budget.py               # new: ledger, state
│   ├── check_state.py              # new: source_check_state
│   ├── source.py                   # modified: SourceFetchLog.outcome
│   └── monitoring.py               # modified: skipped/deferred columns
├── data/repositories/
│   ├── llm_budget_repo.py          # new
│   ├── check_state_repo.py         # new
│   └── source_repo.py              # modified: streak query, log_fetch outcome, rules/cadence setter
├── services/
│   ├── llm_budget.py               # new: DB-backed gate, day boundary, error parsing
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
├── api/{health,monitoring}.py      # modified
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
2. **You apply the migration** before the first deploy that needs it.
3. Deploy, verify `/health`, check the startup log line shows the interval and budget settings.
4. Change `MONITORING_INTERVAL_MINUTES` from 360 back to 60 (or leave it; cost no longer depends on it).
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
| Anyone logged in can trigger a manual monitoring run | Runs obey the budget (bounded harm); operator restriction is in feature 004 (ADR-0012) |

## Decisions (reviewed and settled 2026-10-25)

1. **Outcome model (R-10): accepted.** New nullable `outcome` column, `status` unchanged; provider failures stored as `fail` plus `outcome = provider_*` and excluded from health and coverage by two readers.
2. **Ladder offsets and backfill share (R-02): accepted.** Floors at remaining > 8 (backfill), > 10 (monitoring), > 12 (validation); backfill capped at 6 a day.
3. **Student-triggered discovery counts as user-facing: accepted.**
4. **Fail-open for students** if the budget database is unreachable (background defers): accepted.
5. **Quiet scheduled ticks create no `monitoring_runs` row: accepted, with one addition.** Every tick logs one line with sources due / checked / skipped (and deferred) so the scheduler is provably alive. Manual runs always create a row.
6. **Temporary-unavailable = HTTP 503 with `Retry-After`** and a JSON body: accepted.
7. **HEC rollout: changed.** No category is excluded. Rules are enabled for HEC only after a parity check (rules find at least everything the LLM found, on one saved page with a recorded LLM snapshot, no live call in tests); all differences are flagged to the owner (see Story 5).

## Architecture decision record

ADR-0014 (`adr/0014-llm-budget-gate-and-fetch-outcomes.md`, Accepted 2026-10-25) records the central LLM budget gate, the reserve ladder, provider-signal pauses, the Postgres counter store, the 503 refusal, the `outcome` column with the provider-failure exclusion (which amends ADR-0008's failed-run definition), and the unchanged-page skip semantics. It uses the repo's `adr/` convention.

## Complexity Tracking

No constitution violations to justify. The one non-obvious cost is a second small database transaction per LLM call, accepted for exact, shared counting (research R-04).
