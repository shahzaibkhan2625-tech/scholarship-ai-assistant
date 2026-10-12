# ADR-0007: Re-match and Alert Design — Hard-Constraint-Driven Triggers, Application-Level Idempotency, Alert Step Outside the Source Lock

- **Status:** Accepted
- **Date:** 2026-10-11
- **Feature:** 002-source-monitoring-hardening (US2 — re-match and alert the affected owner, FR-ALERT-1..6)
- **Context:** After `source_monitor` stores a new or field-changed scholarship, US2 re-matches affected owners through the existing `match_scholarship` and alerts them in-app and by email. Four choices have real trade-offs: *when* an outcome is alert-worthy, *how* duplicate alerts are avoided, *where* in the run the (slow) alert step executes, and what is deliberately left out of scope.

## Decision

1. **Alert-worthiness (D1, D3).**
   - **No prior match for the owner** (every owner on a *new* scholarship; an applicant with no earlier match on a *changed* one): alert only when the verdict is `eligible`, `likely` or `possibly`. `unknown_requires_verification` and `not` never alert — an owner with sparse data would otherwise be alerted about every new scholarship.
   - **Prior match exists:** the primary, deterministic trigger is a change in the **hard-constraint result set** — the set of `(criterion, result)` pairs of the new verdict versus the owner's latest prior `matches` row (read *before* re-matching, because `match_scholarship` appends a new immutable row). Evidence text is excluded from the comparison. A verdict change **with an unchanged hard-constraint set does not alert**: it can only come from the soft-preference step, which calls an LLM and is not stable between runs. Both verdicts are recorded in `change_summary` as information only.
   - Hard constraints are never computed outside `match_scholarship`; the soft-preference LLM call inside it is accepted.

2. **Idempotency (D6).** Before re-matching an (owner, scholarship) pair, the service checks for an existing `alerts` row with the same `(user_id, scholarship_id, monitoring_run_id)` and skips it. This is an **application-level check, with no DB unique constraint and no migration**. It is **not race-safe across concurrent runs**; it is only adequate for the current model, where the per-source advisory lock and `has_running_run` mean one run produces the events for a source. If concurrent alert generation is ever introduced, a unique index on those three columns is the fix.

3. **Placement of the alert step (D10).** The alert step runs **after** `_monitor_one_source` returns — i.e. after that source's changes are committed and its advisory-lock transaction has been rolled back. It is not inside `source_lock`. The lock is transaction-scoped on a dedicated connection against a pooled Neon endpoint; keeping it open across per-owner LLM calls and SMTP round-trips would pin a pooled backend for the duration of the slowest external call. Safety is preserved because the change events come only from the run that held the lock, and a failure in the alert step is logged and never touches the stored change.

4. **Bounded work (D4).** A per-run budget (`MAX_ALERT_REMATCHES_PER_RUN`, default 500 owner × event pairs) caps re-matching cost. When exhausted the step logs once and stops generating alerts for the rest of that run; it never raises.

5. **Out of scope (D2, D5, D7, D8).** Closed/reopened/stale lifecycle alerts; a failure counter on `monitoring_runs` (failures are logged); scheduled email retry; email links or a base URL (the email carries scholarship id and name only).

## Consequences

### Positive
- The alert trigger is deterministic and unaffected by LLM flakiness in soft preferences.
- No migration and no new lock; the lock's pooled-connection behaviour is unchanged.
- Cost is bounded per run and scales with real changes, not with the number of monitored sources.

### Negative
- A deadline moved between two dates that both pass leaves the hard-constraint set unchanged, so **no alert is sent** for that change. This is the price of D3.
- A soft-preference-only change (verdict flips, hard constraints identical) never alerts.
- A prior match may predate a profile edit, so a changed hard-constraint set can reflect the profile rather than the scholarship; the alert is still accurate about the owner's current standing.
- The duplicate check is racy under concurrent alert generation (see Decision 2).
- A profile with no hard-constraint criteria evaluates only the deadline, so it can receive alerts for any new scholarship with a future deadline.
- Work lost when the cap is hit is not retried; the owner is simply not alerted for those pairs.

## Alternatives Considered
- **Alert on any verdict change.** Rejected: soft-preference LLM output varies run to run, producing spurious alerts.
- **DB unique constraint on (user, scholarship, run).** Rejected for now: needs a migration for a risk the single-run model does not have.
- **Run the alert step inside `source_lock`.** Rejected: holds a pooled connection open across slow external calls; protects nothing the lock needs to protect.

## References
- Spec: `specs/002-source-monitoring-hardening/spec.md` (FR-ALERT-1..6); data model §5–6; research Decision 5
- Related ADRs: ADR-0006 (monitoring update path)
- Code: `backend/app/services/alerts.py`, `backend/app/workflows/source_monitor/graph.py`
