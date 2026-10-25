# Research: LLM Quota Governance & Cheap Monitoring (003)

Phase 0 output for `specs/003-quota-governance/plan.md`. Each entry is Decision, Rationale, Alternatives, and where the evidence came from. Everything marked **measured** was observed on 2026-10-25 by read-only probes (plain HTTP GETs, read-only SQL, source reading); nothing here called the LLM.

## R-01 Where the budget lives and how priority reaches it

**Decision.** Put the enforcement point inside `app/core/llm.py` (the only module that talks to the provider: `generate_text` line 33, `embed_texts` line 48), but keep `core` free of database imports. `core` defines a small gate interface in a new `app/core/llm_gate.py` (priority and request-type enums, the refusal exceptions, a context variable holding the current priority, and a registry for the installed gate). The database-backed gate lives in `app/services/llm_budget.py` and is installed at application start, in the scheduler process, and in CLI entry points. With no gate installed, calls are refused (fail closed), which also satisfies "a call without a class is refused" (FR-BUDGET-3) for scripts.

Priority is declared with a scoped context manager at the entry points, not by adding a parameter to the roughly twelve call sites:
- HTTP requests: a middleware sets `user-facing` for every request by default.
- `run_source_monitor` and the alert re-match step: `scheduled monitoring`.
- candidate validation step: `candidate validation`.
- one-off jobs (feature 004's re-extraction): `one-off backfill`.

**Rationale.** One choke point means no LLM call can bypass the budget (FR-BUDGET-1). A context variable keeps tool and workflow signatures unchanged. The context must be set in the async middleware, not in a sync dependency, because sync dependencies and endpoints each run in their own copied thread context and a value set in one is not visible in the other.

**Alternatives.** An explicit `priority=` argument on every tool (clearer, but touches many signatures and tests). A decorator on each caller (easy to forget on new code). A global counter inside `llm.py` with no priorities (does not meet FR-BUDGET-3/4).

**Evidence.** `core/llm.py` has no guard, retry or counter. The only code that handles provider errors is `workflows/doc_pipeline/graph.py:143`, which catches `genai_errors.APIError` and `LlmNotConfiguredError`; it must be updated to the new exception base (see R-03).

## R-02 Admission policy (reserve ladder)

**Decision.** Per request type, per budget day, with allowance A and user-facing reserve R (defaults A=20, R=8 from the approved spec):
- `user-facing`: allowed while any allowance remains.
- Background classes are allowed only while the remaining allowance stays above R plus a class offset: `one-off backfill` offset 0, `scheduled monitoring` offset 2, `candidate validation` offset 4 (so validation is cut off first, then monitoring, then backfill).
- `one-off backfill` is also capped at a daily share (default 6) so a large job cannot take the whole background pool.
- When refused, background work is **deferred** (counted, retried later), never failed.
- All numbers are configuration.

**Rationale.** The spec fixes the order (user-facing, then backfill, then monitoring, then validation) and the reserve, but not how scarcity is shared. A ladder of floors gives a strict, easy-to-test order with no per-class borrowing logic. With the defaults, background work can use 12 requests a day: validation stops at 8 used, monitoring at 10 used, backfill at 12 used or 6 used by backfill itself, whichever comes first.

**Alternatives.** Fixed per-class shares (wastes unused share, needs borrowing rules). Strict priority with no floors (a busy low class cannot be protected from a high class that is itself noisy).

**Open for review.** The offsets (0/2/4) and the backfill share (6) are my defaults; the spec only fixed the reserve.

## R-03 Reading the provider's quota signal

**Decision.** Wrap the two provider calls and translate `google.genai.errors.APIError` (`code`, `status`, `details`) into a small hierarchy under a new base `LlmError`: `LlmQuotaExhausted(retry_after)`, `LlmProviderError`, and `LlmResponseUnparseable` (raised by callers' JSON parsing, mapped in the extraction tools). On a 429 the gate records a `provider_exhausted` event and sets a pause.

How long to pause matters, and the real error shows why. The 2026-10-24 06:54 UTC failure logged in `source_fetch_log` for HEC contains a `RetryInfo` of `340s` **and** a `QuotaFailure` whose `quotaId` is `GenerateRequestsPerDayPerProjectPerModel-FreeTier` with limit 20. Retrying after 340 seconds would hit the same daily limit. Rule: if the violation's `quotaId` ends in `PerDay` (or the message says "per day"), pause until the next budget-day boundary; otherwise pause for the provider's `retryDelay` (minimum 60 seconds, default 900 seconds if no hint).

**Rationale.** FR-BUDGET-6/7: the provider's signal is authoritative, and the retry hint alone is misleading for daily quotas. The doc-pipeline handler (`doc_pipeline/graph.py:143`) is switched to catch `LlmError`, so the upload fix keeps working.

**Alternatives.** Trust `retryDelay` only (loops on a daily cap). Parse the human message text (fragile).

**Check at build time.** Assert one HTTP attempt per call: the client is created with defaults in `get_gemini_client`; add a test that fails if client-level retries are ever enabled, because they would break exact counting.

## R-04 Where the counts live

**Decision.** A Postgres ledger table plus a small pause-state table (see `data-model.md`). Reservation is atomic: inside one short transaction on its own session, take `pg_advisory_xact_lock` keyed by (budget day, request type), read the day's `allowed` total and the pause row, decide, and upsert `count = count + 1`. The same table family also stores deferred, denied, provider-exhausted, discrepancy and avoided-call counters, so every FR-OBS-1 number survives restarts and is shared by all workers.

**Rationale.** FR-BUDGET-8 requires shared, restart-safe counting. The code already uses advisory transaction locks for per-source monitoring (`scheduling/locks.py`), so the pattern fits the pooled Neon endpoint. Volume is tiny (about 20 reservations a day), so contention and cost are negligible: one extra short transaction per LLM call.

**Alternatives.** In-process counter (lost on restart, wrong with N workers). Counting rows in `source_fetch_log` (cannot see user-facing calls). Redis (not in the stack, not justified).

**Failure behaviour.** If the gate cannot reach the database: background classes are deferred (FR-BUDGET-9); user-facing calls proceed and rely on the provider's own limit, with the failure logged and counted. (My default; flagged for review.)

## R-05 Budget-day boundary

**Decision.** The budget day is the calendar day in `America/Los_Angeles` (configurable), matching the observed reset at midnight Pacific. `zoneinfo` resolves it with no new dependency: verified working on the laptop and inside the production container (`ZoneInfo("America/Los_Angeles")` returned 14:48 local on 2026-10-24 on both).

## R-06 Change detection: what to fingerprint, and what conditional requests can do

**Measured evidence for the two monitored sources.**

| Probe | HEC (`www.hec.gov.pk`) | EACEA (`www.eacea.ec.europa.eu`) |
|---|---|---|
| Server | Microsoft-IIS/10.0 (SharePoint) | not reported |
| `ETag` | none | none |
| `Last-Modified` | present, but equals the response time (21:45:29 then 21:45:37 on two fetches 8 s apart) | none |
| `If-Modified-Since` with that value | **200 with a full body** (ignored) | not applicable |
| Raw body, two fetches 8 s apart | **differs** (136,195 vs 136,156 bytes; generated element ids and script hashes change each request) | not tested |
| Visible text (scripts/styles/tags stripped, whitespace collapsed), two fetches | **identical SHA-256** (3,593 chars) | not tested |

**Decision.** Fingerprint = SHA-256 of the page's normalised visible text, using the existing `clean_html_to_text` in `tools/extract_listing.py` (it already strips scripts, styles and tags and collapses whitespace). Never fingerprint raw bytes: for HEC that would report "changed" on every check and save nothing. Conditional requests (`If-None-Match` / `If-Modified-Since`) are implemented opportunistically and stay off unless the server proves it honours them (a stable ETag, or a `Last-Modified` that has stayed constant across checks with an unchanged fingerprint). **For the two current sources conditional requests give no benefit**, so they are the lowest-value, last-built part of story 1; the saving comes entirely from the fingerprint.

**Known limit.** A visible-text fingerprint does not see a link-only change (same names, new `href`). The forced full re-check (R-07) bounds how long that can go unnoticed.

**Alternatives.** Hash raw HTML (useless for HEC). Hash extracted items (requires the LLM first). Compare the DOM structure (over-engineered).

## R-07 Where the skip sits and what "unchanged" does to records

**Decision.** In `sources/connectors/official_fetch.py:fetch_and_extract_listing`, after the fetch succeeds and the redirect checks pass (current line ~396-416) and before candidate-link detection and `extract_listing` (lines 418-420), compute the fingerprint and compare with the stored state:
- same fingerprint, last extraction succeeded, not due for a forced full check: return a new `unchanged` result and skip candidate-link detection too (same page, same links).
- otherwise continue as today; store the new fingerprint **only after a successful extraction** so a deferred or failed extraction is retried against the old baseline.

In `source_monitor/graph.py:_monitor_one_source`, an `unchanged` result takes its own branch: touch the check as a success and apply the healthy health transition, then **skip change detection, `mark_seen_verified` and the stale sweep entirely**. Running the sweep with an empty listed-names set would look like "everything was removed" and could close records; that must not happen (FR-SKIP-6).

Because skipped checks no longer refresh `last_verified_at`, the forced full check interval is `min(7 days, freshness window / 2)` per source. The default freshness window is 180 days (`services/verification.py:17`), so 7 days applies today.

For discovery (`bypass_cache=False`), an `unchanged` result is mapped by `listing_fetch_tool` to the existing `cached` outcome, so a student-triggered discovery on an unchanged page also costs no LLM request.

## R-08 Unextractable pages and history bootstrap

**Decision.** If the page is unchanged but the last extraction did not succeed (zero grounded candidates, provider failure), do not treat it as unchanged: retry with doubling back-off starting at 60 minutes, capped at the source's cadence interval, stored as `next_attempt_at` and `backoff_level` on the per-source state. A changed page resets the back-off.

On the first run after deployment there is no state row, which would restart EACEA's back-off from zero and spend about five extra calls on day one. So when no state row exists, bootstrap `backoff_level` from `source_fetch_log` by counting the most recent consecutive "listing extraction yielded 0 candidates" rows (EACEA has 136 of 136 in the last 7 days).

## R-09 Per-source cadence and the scheduler tick

**Decision.** Keep the APScheduler interval job, but make it cheap: each tick loads the monitorable sources and their state, selects the ones that are due, orders them stalest-first, and returns without creating a `monitoring_runs` row when nothing is due and no pending candidate needs validation (manual runs always create a row). Every tick, idle or not, logs one structured line with sources due, checked, skipped and deferred (owner decision 2026-10-25), so an idle scheduler is distinguishable from a dead one. Due = `max(next_attempt_at, last_checked_at + interval)`; `last_checked_at` already exists on `source_registry` and is updated by `touch_source_check` (`source_repo.py:287`), so no new schedule column is needed.

Interval = operator override if set, else by `update_frequency`: weekly 1 day; monthly, quarterly, annual 7 days; none 1 day (approved defaults). It is capped by the freshness window so schedule alone cannot make a record stale (FR-CADENCE-4). The override lives under a namespaced key (`check_interval_minutes`) in the existing `extraction_rules` JSON, validated on write.

With cadence gating, the tick interval no longer drives LLM cost, so `MONITORING_INTERVAL_MINUTES` can return from the 360-minute stopgap to 60 once this ships.

## R-10 How outcomes are represented in `source_fetch_log`

**Measured constraint.** `source_fetch_log.status` is `VARCHAR(7)` with no CHECK constraint. Existing values are `ok`, `fail`, `timeout` (the longest is 7 characters), so a status such as `unchanged` (9) would not fit without widening the column.

**Decision.** Do not add status values. Add one nullable column `outcome VARCHAR(24)` and keep `status` as `ok | fail | timeout`:

| Situation | `status` | `outcome` | `items_found` |
|---|---|---|---|
| Page unchanged, extraction skipped | `ok` | `unchanged` | NULL |
| Extraction deferred by budget or pause | `ok` | `deferred` | NULL |
| Extracted with rules, no LLM | `ok` | `rules` | count |
| Extracted with the LLM | `ok` | NULL (as today) | count |
| LLM quota exhausted | `fail` | `provider_quota` | NULL |
| LLM outage / other provider error | `fail` | `provider_error` | NULL |
| LLM response unparseable | `fail` | `provider_parse` | NULL |

The existing readers keep working for the new success-type rows because they look for `status == ok AND items_found IS NOT NULL` (`official_fetch.py:259`, `source_monitor/graph.py:171`, `retention.py:72`): rows with `items_found` NULL are ignored when finding the last real extraction, so the baseline stays on the last true extraction.

The provider-failure rows are `fail`, which the failure-streak definition would count (`source_repo.py:338`, `bool_or(status != OK)`, the single shared definition used by health and retention) and `coverage.py:63-75` would count as failed. Both must exclude rows whose `outcome` starts with `provider_`. This is exactly the bug seen on 2026-10-24: the 429 on HEC went through `_record_crashed_check` (`source_monitor/graph.py:397-416`), which writes a `FAIL` row and applies a health failure.

**Alternatives.** Widen `status` and add members (touches every `status` reader). Encode the reason in `error` text (the codebase already prefixes `[monitor]`, but filtering by text is fragile). Store provider failures as `ok` with an outcome (hides a failed extraction behind a healthy-looking status).

**Open for review.** Using `fail` for provider rows keeps the status truthful but relies on two readers being updated and tested; this is my recommendation.

## R-11 Deterministic extraction (story 5): feasibility on HEC, and not on EACEA

**HEC listing page, measured** (`https://www.hec.gov.pk/english/scholarshipsgrants/Pages/default.aspx`, plain GET, 200, ~136 KB):

| Finding | Value |
|---|---|
| Rendering | Server-rendered HTML. Not JS-rendered. |
| Card markup | `<li data-position='NAME' class='card card-info-listing line-content'><div class='desc'>NAME</div> ... <ul class='card-actions'><li id='TobeAppendN'>CATEGORY</li></ul><a class='read-more-arrow' href='/english/scholarshipsgrants/Pages/X.aspx'>` inside `ul.card-info-container.list-view-display` |
| Cards | 43 |
| Unique names | 38 (one name, "Alumni Gateway", repeats) |
| Unique detail links | 43 |
| Categories | International 11, Special Initiatives 11, National 8, Scholarship for Foreigners 7, Support Programs 3, Learning Opportunities Abroad 2, Fellowship Programs 1 |
| Fields present on the card | name, category, detail link. **No deadline, funding or degree level.** |
| Stability | Visible text identical across two fetches; raw bytes differ |
| Current LLM result for comparison | 32 items (`items_found` on the newest OK row) |
| Parser | `beautifulsoup4` is already a declared dependency; no new dependency needed |

**Verdict: feasible.** A declarative rule set (item selector, name from the `data-position` attribute, detail URL from the arrow link, optional category text, minimum item count, anomaly thresholds) reproduces everything the LLM can read from this page, deterministically and for free. It cannot produce deadlines or eligibility: those only exist on the 43 detail pages, which is feature 004's job (ADR-0013), so deterministic rules here do not make 004 unnecessary.

**What changes when rules replace the LLM for HEC.**
- Rules return up to 38 unique items against 32 from the LLM. The extra items include entries that may not be scholarships (for example some "Support Programs"). They would be ingested as new records and, once users exist, trigger "new scholarship" alerts. **Owner decision (2026-10-25): no category is excluded.** Instead, rules are enabled for HEC only after a parity check: the rules and the LLM are run over the same saved page (a committed fixture plus a committed, once-recorded LLM snapshot, so tests make no live call), and the check passes only if the rules find at least every item the LLM found, compared by normalised name. The report lists `matched`, `LLM-only` (blocks enabling) and `rules-only` (flagged to the owner, not blocking). A second read-only report compares the rule output with the stored scholarships. The rule schema therefore has no category-exclusion field.
- Names are read verbatim from the page and still go through the same grounding gate at threshold 1.0 (FR-RULES-2). Grounding must compare against text decoded by the HTML parser, because the existing `clean_html_to_text` does not unescape entities (a name containing `&` appears as `&amp;` in raw markup).
- Provider is taken from the registry's `organization` field only if set; country, field, degree and funding stay unknown (no guessing, constitution Principle I).

**EACEA: not feasible now.** The earlier read-only GET showed substantial server-rendered text (~47 K visible characters, 60 `ecl-card` elements) dominated by an alphabetical university/institution list, with programme names not cleanly separable, and 136 of 136 recent LLM extractions grounded nothing. Rules for it belong to feature 004 once the page structure is understood. Under 003 EACEA benefits instead from the unextractable-page back-off (R-08).

**Operator path, and a hazard found while planning.** There is no API to edit a registry entry. The only write path is the seed loader, `upsert_source_by_domain` (`source_repo.py:169`), which fully replaces every field except `listing_page_url` and `freshness_window_days` (`_PRESERVE_IF_OMITTED`, line 31). Re-running the seed on production would reset EACEA's `status` from `failing` back to `active` and replace `extraction_rules` wholesale. So the plan adds a small dedicated command that validates and sets only the new rule and cadence keys on one source, and the HEC rule set is also written into `seed_sources.yaml` so a fresh database has it. Production is never re-seeded for this feature.

## R-12 Observability

**Decision.**
- Prometheus: a collector that reads the ledger and pause rows at scrape time, so values persist across restarts and workers. Gauges: allowance, used, remaining, reserve, seconds until reset, provider-pause deadline; counters (as gauges of the ledger totals): deferred, denied, provider-exhausted, discrepancy, avoided-unchanged, avoided-rules, by request type and priority class. Existing `/metrics` authentication is unchanged (FR-OBS-4).
- Per-source: `checks` and `changed` over the last N checks (state table) produce a `noisy` flag exposed as a gauge (FR-SKIP-7).
- Startup log: one structured line from `start_scheduler` (`scheduling/scheduler.py:80`) with the tick interval, cadence defaults, per-source overrides (best-effort DB read), and budget settings. This is the owner's note from the Task B review.
- Run summary: add skipped and deferred counters to `monitoring_runs` and to the run-finished log line (currently `_record_run_end`, `source_monitor/graph.py:525`, reports processed/failed/changes only).

## R-13 Test strategy without live calls

- Unit: gate admission (ladder, reserve, share cap, pause, day boundary, fail-closed/fail-safe), error classification (including the real daily-quota error shape captured above as a fixture), fingerprint normalisation, cadence selection and back-off, rule extraction against a trimmed copy of the real HEC markup.
- Integration: monitoring run twice on an unchanged fixture (second makes zero provider calls), changed fixture (one call), deferred extraction leaves records untouched, provider failure does not move health, discovery path records outcomes, `503` with `Retry-After` for user-facing refusal.
- DB-touching gate tests use the existing per-test rollback fixture by injecting its session factory, because the real gate opens its own short transaction and would otherwise write to the shared Neon database.
- One new scored eval case (`quota_governance`, threshold 1.0) simulating a day of mixed traffic with a fake provider and asserting the invariants: never above allowance, reserve honoured, no background call below the floor, no provider call after a quota pause. This follows constitution Principle V.
- CI makes no live provider calls; the existing smoke tests stay deselected.

## R-14 Migration and rollout

See `plan.md` for the migration statement. In short: one additive revision; old code runs unchanged on the new schema, so migrate first, then deploy, then (optionally) set the tick back to 60 minutes.
