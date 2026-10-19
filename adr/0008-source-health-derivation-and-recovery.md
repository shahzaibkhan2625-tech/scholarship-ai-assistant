# ADR-0008: Source Health Is Derived From `source_fetch_log` by Monitoring Run, and Failing Sources Stay Fetchable for Recovery

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-12
- **Feature:** 002-source-monitoring-hardening (US3 — source health, FR-HEALTH-1..6, O-2)
- **Context:** US3 must show, per registry source, whether its latest fetch worked, and must flag a source after N consecutive failures without ever presenting a failed fetch as "no scholarships found." Three facts about the existing code shape the design:
  1. **One check is several `source_fetch_log` rows.** A monitored fetch writes an `OK` row for the HTTP success (`fetch_official`) and then a second row from `fetch_and_extract_listing` (`OK` with `items_found`, or `FAIL` for empty extraction / redirect anomaly). The ingestion workflow additionally writes its own per-scholarship rows (`OK, items_found=1`, or `FAIL "[stage] …"`) with no URL and no run id. A raw "last N rows" count is therefore wrong: a failed extraction reads `OK, FAIL, OK, FAIL…` and never looks like consecutive failures.
  2. **Failing sources cannot currently be fetched.** `get_source_by_id` / `get_active_sources` gate on `status == active` (Principle IV), and the connector raises `SourceNotApprovedError` for anything else. O-2 requires a failing source to stay in the schedule and recover on one success, which that gate forbids.
  3. **Two existing failure rules disagree.** The 001 discovery path flips a source to `failing` on its first failed fetch (`mark_source_failing`). 002's D5 made monitoring pass `skip_failing_transition=True` so that one failed attempt never removes a source from the active set — but that left monitoring with no way to ever produce `failing` at all, while FR-HEALTH-4 requires "N consecutive failures → failing".

## Decision

1. **Health is derived, not stored. No new table, column or migration.**
   - `status`, `last_checked_at`, `last_success_at`, `listing_page_url` are the existing `source_registry` columns (the monitor already maintains the timestamps through `touch_source_check`).
   - `consecutive_failures` is computed from `source_fetch_log` **grouped by `monitoring_run_id`**: each (source, run) pair yields exactly one outcome — *failed* if any of that source's rows in the run is not `OK`, else *ok* — and the count is the number of most-recent consecutive runs whose outcome is failed. Rows with a NULL `monitoring_run_id` (on-demand discovery, ingestion stage rows) are **excluded**; they are neither failures nor resets. The scan is bounded (most recent 100 runs per source) and set-based for the list endpoint.
   - To make "most recent" well-defined, `log_fetch` now stamps `started_at` from the application clock when the caller omits it. The database `now()` default is the *transaction* start time, so rows written in one transaction (tests, and any future batched writer) tie and make run ordering ambiguous.

2. **A failed check is always a recorded `FAIL` row, including crashes.** If the per-source work raises after the fetch (e.g. the extractor crashes), the run's exception handler now writes a `FAIL` row carrying the run id and the error, and touches `last_checked_at` — otherwise a crashed check would leave only the `OK` HTTP row and read as healthy.

3. **Monitoring flips status by the derived count, using `HEALTH_WINDOW_N`.** After each source check, inside the existing per-source flow: a failed check on an `active` source whose `consecutive_failures >= HEALTH_WINDOW_N` is set to `failing`; a successful check on a `failing` source calls `reactivate_source` (guarded `failing → active` only) **before** change detection, so ingestion (which re-checks the source gate) sees an active source. `disabled` and `pending` are never touched. The single `HEALTH_WINDOW_N` setting is deliberately shared with the future US8 retention floor.

4. **Failing sources stay fetchable for monitoring only — an additive gate widening.**
   - `fetch_official` and `fetch_and_extract_listing` gain `allow_failing: bool = False`. Default behaviour is byte-for-byte the 001 gate. With `allow_failing=True` the lookup is `source_repo.get_monitorable_source_by_id` (`ACTIVE | FAILING`).
   - `source_repo.get_monitorable_sources` is the monitor's selection query (`ACTIVE | FAILING`, same `discovery_role` / `access_method` filters). `get_active_sources` and `get_source_by_id` are unchanged, so discovery, Q&A, matching and search still never see a failing source.
   - `PENDING` and `DISABLED` remain blocked everywhere, including with `allow_failing=True` (Principle IV preserved). A failing source is still an *approved* source that is degraded, not an unapproved one.

5. **The D5-vs-FR-HEALTH-4 conflict is resolved by giving discovery and monitoring different failure-marking rules, on purpose.**
   - **Discovery (001, unchanged):** first failed fetch → `failing` immediately. A user-initiated search should stop trusting a source that just failed.
   - **Monitoring (002):** `failing` only after `HEALTH_WINDOW_N` consecutive failed *runs*. A scheduled job that touches every source repeatedly should not demote a source because of one transient blip (D5), but must surface a persistent problem (FR-HEALTH-4).
   - Consequence the operator will see: a source can read `failing` with `consecutive_failures < N` (discovery flipped it). This is expected, not a bug; monitoring recovers it on the next successful run.
   - Task T202's original wording ("mark the source failing via `mark_source_failing`" on extraction failure) is superseded by decision 3; "flag for re-check" (FR-HEALTH-5) is satisfied by the `FAIL` row plus the source remaining in the schedule.

## Consequences

### Positive

- No migration and no counter to drift: the streak cannot disagree with the log it is computed from.
- Every failed check, including crashes, is visible in `/sources/{id}/fetch-log` and counted once; the health view cannot be fooled by multi-row checks or ingestion noise.
- Recovery needs no operator action, and the gate widening is opt-in per call and limited to one extra status.

### Negative

- The health view and the status flip depend on `monitoring_run_id` being set on every monitored row. Any future monitored code path that logs without it is invisible to the streak.
- The streak is computed per request (bounded query). Fine at registry scale; revisit if the registry grows by orders of magnitude.
- Two failure rules (discovery vs monitoring) must be kept in mind together; the asymmetry is documented here rather than eliminated.
- `log_fetch` now uses the application clock for `started_at` when omitted; this is a small behavioural change for all callers (timestamps are no longer DB-clock), accepted because ordering within a run matters more than clock source.

### Known gaps (named, deferred)

- **API-access sources never recover under monitoring.** The monitor selects `discovery_role=True` and `access_method="web"`, so a `failing` API-type source is never re-checked by it. Out of scope for US3; a later slice must decide how API sources are health-checked.
- **US8 retention must be defined in runs, not rows.** One check writes 2–3 rows (and ingestion adds more), so a floor of `HEALTH_WINDOW_N` *rows* per source can retain fewer than N *checks* and erase the history that explains a `failing` flag. The retention job (T228) and its tests (T219) must preserve at least the last `HEALTH_WINDOW_N` monitoring runs' rows per source.
- **Resolved in US8 B2:** the runs-not-rows retention floor above shipped as ADR-0011 decision 2 (floors a/b/c, `python -m app.services.retention`), sharing `source_repo.ranked_monitoring_runs` with health derivation. The bullet is kept as the record of why.
- `api_connector.py:145` calls `mark_source_failing` unconditionally. The monitor's `graph.py` does not import it, so the monitor cannot reach it; not traced further (low priority).

## Alternatives Considered

- **Persist a `consecutive_failures` counter on `source_registry`.** Rejected: needs a migration and two writers (monitor + discovery) that can disagree with the log; derivation is cheap at this scale.
- **Count the last N raw `source_fetch_log` rows.** Rejected: one check is 2–3 rows and ingestion adds unrelated rows, so the count is not "N checks".
- **Mark `failing` on the first failed monitoring check (reuse `mark_source_failing`).** Rejected: contradicts D5 and FR-HEALTH-4 and would drop a source from discovery on a single transient error.
- **Make `get_source_by_id` / `get_active_sources` include `failing`.** Rejected: silently exposes degraded sources to discovery, Q&A, matching and search, which is the opposite of what the status is for.
- **Align discovery to the N-run rule.** Rejected for now: it would change shipped 001 behaviour; the asymmetry is documented instead.

## References

- Spec: `specs/002-source-monitoring-hardening/spec.md` (US3, FR-HEALTH-1..6)
- Tasks: T197–T204, O-2
- Related ADRs: ADR-0001 (source governance / status gate), ADR-0006 (monitoring update path)
