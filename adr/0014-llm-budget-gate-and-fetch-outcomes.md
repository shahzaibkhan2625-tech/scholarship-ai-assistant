# ADR-0014: Central LLM Budget Gate and a Fetch-Outcome Model That Keeps Provider Failures Out of Source Health

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-25
- **Feature:** 003-quota-governance (stories 1, 2, 3, 4, 6; `specs/003-quota-governance/plan.md`, `research.md` R-01..R-10)
- **Context:** The system runs on a Gemini free tier of about 20 generation requests a day (ADR-0012). Scheduled monitoring alone is estimated at about 48 requests a day because every check calls the LLM, even for an unchanged page (`source_monitor/graph.py:447`, `bypass_cache=True`). Nothing counts requests, nothing separates a student's request from background work, and a provider error is indistinguishable from a site failure: on 2026-10-24 a quota error for HEC went through `_record_crashed_check` (`source_monitor/graph.py:397-416`) and was written as a failed fetch that counts toward the source's health streak. Separately, `source_fetch_log.status` is `VARCHAR(7)` (`ok`, `fail`, `timeout`), so it cannot carry new outcomes such as "unchanged" without a schema change, and the status is read by health, coverage and retention. These problems are linked: controlling LLM spend requires both a place to enforce a budget and an honest record of why a check did or did not call the LLM.

## Decision

Treat the following as one cluster, because the pieces only work together and would change together.

1. **One enforcement point.** Every provider call passes through a gate invoked from `app/core/llm.py` (`generate_text`, `embed_texts`). `core` holds only the interface (`app/core/llm_gate.py`: priority and request-type enums, exceptions, a context variable, a gate registry) and imports no database code. The database-backed gate is `app/services/llm_budget.py`, installed at application start, in the scheduler and in CLI entry points. With no gate installed a call is refused (fail closed).
2. **Priority is declared at entry points** with a scoped context manager, not threaded through call signatures. HTTP requests default to `user_facing` via middleware; the monitor run and alert re-match are `monitoring`; candidate validation is `validation`; one-off jobs are `backfill`. A call with no class is refused.
3. **Admission is a reserve ladder** per request type and budget day (allowance 20 and user reserve 8 by default): user-facing may use any remaining allowance; background classes are admitted only while the remaining allowance stays above the reserve plus a class offset (backfill 0, monitoring 2, validation 4), and backfill is capped at a daily share of 6. Refused background work is **deferred**, counted, and retried, never failed. All values are configuration.
4. **The provider is authoritative.** A 429 pauses non-user-facing calls; if the violated quota is per-day the pause lasts to the next budget-day boundary (midnight `America/Los_Angeles`), otherwise it follows the provider's retry delay. A pause with a lower local count is recorded as a `discrepancy`.
5. **Counts live in PostgreSQL.** A counter ledger and a small pause-state table, updated atomically under an advisory transaction lock on a separate short session, so counting is exact across workers and restarts. If the budget store is unreachable, background work defers and student requests proceed to the provider's own limit.
6. **Students get a clear refusal.** When the allowance is spent or a pause is active, LLM-backed routes return HTTP 503 with `Retry-After` and a structured body, with no provider call.
7. **Fetch outcomes get their own column.** `source_fetch_log` keeps `status` as `ok | fail | timeout` and gains a nullable `outcome` column. Success-type outcomes (`unchanged`, `deferred`, `rules`) are stored as `ok` with `items_found` NULL, so the readers that look for the last real extraction (`status = ok AND items_found IS NOT NULL`) are unaffected. Provider failures (`provider_quota`, `provider_error`, `provider_parse`) are stored as `fail` with a `provider_*` outcome, and the shared failure-streak definition (`ranked_monitoring_runs`, ADR-0008) and the coverage failed-source count **ignore** them, so a provider failure never moves a source toward `failing` and never reads as "no scholarships found".
8. **An unchanged page is skipped, not re-confirmed.** A fingerprint of the page's visible text decides whether extraction runs. An `unchanged` check records a successful check and bypasses change detection, `mark_seen_verified` and the stale sweep, because running the sweep against an empty listing would look like a mass removal. A forced full re-check at `min(7 days, freshness window / 2)` keeps records fresh.
9. **A scheduled tick with nothing due creates no run row** but logs one line per tick (sources due, checked, skipped), so the scheduler is provably alive without adding empty run records.

## Consequences

### Positive

- LLM spend has one place to be limited, observed and tuned; adding a future LLM-backed feature cannot silently bypass it.
- Students keep a protected reserve even when background work is heavy, and a spent allowance produces a clear, retryable response instead of a 500.
- Provider failures stop corrupting source health, a defect already seen in production.
- Estimated steady-state background use drops from about 48 to under 1 request a day (estimate), leaving the free tier usable for students and for feature 004's re-extraction.
- The paid-tier decision at feature 006 becomes a configuration change.
- No existing status value, column type or reader contract changes.

### Negative

- Every LLM call now pays one extra short database transaction, and a Neon cold start can add latency to a student request (mitigated by failing open for students).
- Priority classification is by convention at entry points; a new path that forgets it is refused loudly, but only a test catches it before runtime.
- Two readers (the streak definition and coverage) must stay in step with the `provider_*` rule; a future reader that forgets it would treat a provider failure as a site failure again. Tests pin both.
- The visible-text fingerprint cannot see a link-only change; the weekly forced re-check bounds how long that goes unnoticed.
- Counting is by request, not tokens, and cannot see other consumers of the same key (laptop work, smoke tests); the provider's signal covers that but only after the fact.
- `ranked_monitoring_runs` is shared by health and retention (ADR-0008, ADR-0011), so the exclusion changes both; this amends ADR-0008's definition of a failed run.

## Alternatives Considered

- **Explicit `priority=` parameter on every LLM-backed function.** Clearer at each call, but touches about a dozen signatures and tests; a context scope at entry points gives the same guarantee with less churn.
- **Per-class fixed shares of the allowance.** Simple, but unused share is wasted and needs borrowing rules; the ladder gives a strict order with no borrowing.
- **An in-process counter, or counting from `source_fetch_log`.** An in-process counter is lost on restart and wrong with several workers; the fetch log cannot see student-triggered calls. Redis would add a service the stack does not have.
- **Widen `status` and add members (`unchanged`, `deferred`, `provider_*`).** Touches every status reader and the column length; a nullable annotation column is additive and backward compatible.
- **Encode the reason in the `error` text.** The codebase already uses prefixes such as `[monitor]`, but filtering health on text is fragile.
- **Store provider failures as `ok` with an outcome.** Avoids touching the streak and coverage readers, but hides a failed extraction behind a healthy-looking status. Chosen against, with the cost that two readers need explicit handling.
- **Rely on provider retry hints alone.** The real 2026-10-24 error carried a 340 second retry delay on a per-day quota, so retrying after the hint would hit the cap again.
- **Hash raw HTML for change detection.** HEC's raw bytes differ on every fetch while its visible text is identical, so a raw hash would report a change every time and save nothing.

## References

- Plan and design: `specs/003-quota-governance/plan.md`, `research.md` (R-01 to R-10), `data-model.md`, `contracts/api-delta.md`
- Spec: `specs/003-quota-governance/spec.md` (FR-BUDGET, FR-SKIP, FR-CADENCE, FR-ERR, FR-OBS)
- Related ADRs: ADR-0008 (source health derivation: its failed-run definition is amended here), ADR-0011 (retention floors share that definition), ADR-0012 (free-tier stance and build order), ADR-0013 (verification semantics, unaffected), ADR-0007 (re-match budget: deferral skips a pair)
- Evidence: `docs/blueprint-gap-analysis.md` (M-23, M-33, M-35, M-36)
