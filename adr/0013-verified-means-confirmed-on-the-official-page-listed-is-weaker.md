# ADR-0013: "Verified" Means Confirmed on the Scholarship's Own Official Page; Listing-Only Records Are "Listed"

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-10-25
- **Feature:** Cross-feature semantics, implemented in 004 (trustworthy records); amends the meaning set by ADR-0009
- **Context:** Today `verification_status = VERIFIED` is set by `scholarship_repo.mark_confirmed_by_monitoring` when monitoring re-sights a record on an approved registry listing (ADR-0009). No call site ever passes `official_source_confirmed=True`. A live read on 2026-10-25 found 34 of 34 scholarships `VERIFIED` while 34 of 34 have no deadline and none has any requirement row. The label therefore claims more than is known, which conflicts with Blueprint §32 ("official source is the final authority"), PRD FR-VERIFY-1/3 and the charter's "never fabricate" rule.

## Decision

1. **`verified` is reserved for records confirmed on the scholarship's own official page.** A record may be `verified` only when all of the following hold:
   - its own official scholarship page (not a listing that merely names it) was fetched from a registry-approved source and extraction succeeded;
   - a **deadline** is present, meaning a known date, or an explicit non-date state recorded with its own value-status (for example rolling or not applicable). An unknown deadline does not qualify;
   - **eligibility** is present, meaning at least one known eligibility requirement was extracted and grounded.
2. **Records seen only on a listing get a separate, weaker status, `listed`.** `listed` means "named on an approved registry listing page; details not confirmed". It is the state monitoring may assign on re-sighting. It never produces `verified`.
3. **Presentation and downstream use.** Everything that consumes records treats `listed` as not verified: Q&A and matching label facts from a `listed` record as Inferred or Unknown, never Verified; alerts and rankings may include `listed` records but must show the status; coverage counts report `verified` and `listed` separately and never merge them into "verified opportunities".
4. **Existing data is migrated, not trusted.** The 34 current `VERIFIED` rows do not meet the bar (none has a deadline or requirements) and are reclassified to `listed` by a data migration in feature 004, then promoted to `verified` only after the official-page confirmation step succeeds for them.
5. **`mark_confirmed_by_monitoring` changes meaning**: it will set `listed`, not `verified`. The lifecycle mapping in ADR-0009 (what `verified` lifecycle state requires) is revisited in 004's plan so that lifecycle and verification status do not contradict each other.

## Consequences

### Positive

- The `verified` label can be trusted: it implies a date and eligibility from the scholarship's own page.
- Matching and Q&A can no longer present listing-only guesses as verified facts.
- The weakness found in the gap analysis (M-20) is closed without changing the registry governance model.

### Negative

- The verified count drops from 34 to 0 until the official-page confirmation step exists, so coverage and "verified opportunities" numbers look worse before they look better.
- Official-page confirmation costs additional LLM extraction calls (roughly one per scholarship, a one-off backfill of about 34 plus new records, estimate); 004 must throttle this under 003's budget guard.
- The rule needs a precise definition of "eligibility present" and of a non-date deadline state; those details belong to 004's spec and plan, not this ADR.
- It touches `verification_status`, lifecycle derivation, coverage and alert code, so 004 is wider than a pure data fix.

## Alternatives Considered

- **Keep the current meaning and document it.** Rejected: the label would continue to overstate what is known.
- **Rename `verified` to `listed` everywhere and never introduce a stronger status.** Rejected: the Blueprint and PRD require a genuine verified state with official-source confirmation.
- **Require only the official page, without deadline and eligibility.** Rejected by the owner: a confirmed page with no deadline or eligibility is not yet useful or checkable.

## References

- `docs/blueprint-gap-analysis.md` (M-20, §4.d, §6 feature 004)
- Blueprint §32; PRD FR-VERIFY-1, FR-VERIFY-3, FR-VERIFY-5
- Related ADRs: ADR-0006 (monitoring update path), ADR-0009 (lifecycle confirmation), ADR-0012 (roadmap order)
