# ADR-0004: `submission_approvals` Is Append-Only, Scope-Bound, and Content-Bound

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-09-21
- **Feature:** 001-scholarship-mvp (Phase 4 Slice — T118/T119/T120, `submission_approvals` schema)
- **Context:** constitution Principle III and FR-APP-3 require that a human approval "MUST be scoped to the exact submission being sent — an earlier general 'go ahead' does not carry forward to a different or later submission," and that "no code path may treat an existing `submission_approvals` row as covering any submission other than the one identified by its `submission_scope`" (data-model.md §9). data-model.md §9's field list for `submission_approvals` (`id, application_id, submission_scope, approved_by, approved_at, notes`) is not itself present in Blueprint §14 — it is a Phase 4 addition data-model.md introduced ahead of any live-send capability, "so the schema doesn't need to change when Phase 6 adds live sending." Building the model in this slice (T118) surfaced a gap in that field list: `submission_scope` alone identifies *which* submission event an approval covers, but nothing in the row records *what content* (which version of the assembled application case) the human actually reviewed when approving it. Without that, a stale approval whose `submission_scope` string happens to still match a later, materially-changed submission attempt could be silently treated as still valid — exactly the failure Principle III exists to prevent, just shifted from "wrong scope" to "right scope, wrong content."

## Decision

Add `content_fingerprint` (a non-nullable string column) to `submission_approvals`, alongside `submission_scope`, `application_id`, `approved_by`, `approved_at`, and `notes` (data-model.md §9's original list, field names taken verbatim). A row now authorizes exactly one `(submission_scope, content_fingerprint)` pair, not `submission_scope` alone — re-checkable later by comparing both fields against the submission actually being attempted, without consulting agent memory or any prompt.

The table remains strictly append-only:

- No `updated_at` column, and no column with an update-oriented meaning.
- The repository surface (`app/data/repositories/application_repo.py`, T120) exposes INSERT (`create_submission_approval`) and SELECT (`list_submission_approvals`) only — no update or delete function is defined for this table anywhere.
- The SQLAlchemy model (`app/models/application.py`) defines no mutation path; a new submission attempt requires a new row, per data-model.md §9's existing state-transition note.

**Deliberately not decided here:** the exact content-fingerprint algorithm (e.g. a SHA-256 digest of the assembled case's serialized content, vs. a monotonic version/sequence number tied to the application's generated-materials state). `content_fingerprint` is typed as an opaque string with no algorithm enforced at the schema or repository layer. This slice (T118/T119/T120) only adds the storage column and INSERT/SELECT access; computing what goes into it is the `submit_prep` workflow's responsibility (T123, out of scope here) and its final-gate check (out of scope here) is the only place that will need to decide the algorithm and re-derive a fingerprint to compare against a stored approval.

## Consequences

### Positive

- Closes the specific gap this slice found: a stored approval can be invalidated by either a changed scope *or* a changed content state, purely by comparing two stored fields — no dependence on conversational/agent memory to know "what did the human actually see."
- Keeps the append-only guarantee (Principle III's core safety property) intact — adding a column is additive to both the schema (Alembic migration, T119) and the invariant; it does not introduce any new way to mutate a row.
- Defers the harder, still-open design question (fingerprint algorithm) to the workflow that actually needs to answer it (`submit_prep`, T123), rather than guessing it now under this slice's narrower scope (models/migration/repository/schemas only).

### Negative

- **Known limitation, unresolved:** until `submit_prep` (T123) is built, nothing computes or validates `content_fingerprint` — the column exists and can be written with any string a caller supplies. The repository layer trusts its caller to pass a real fingerprint; enforcing that it was actually derived from the assembled case is future work.
- A future engineer choosing the fingerprint algorithm could pick a scheme that changes on cosmetic edits (e.g. whitespace/formatting-only regeneration) and unnecessarily invalidates an approval whose actual content didn't materially change — this ADR intentionally leaves the false-invalidation-vs-false-validation tradeoff to that later decision rather than presupposing one.
- This is a schema extension beyond both Blueprint §14 and data-model.md §9's original text; a reader relying on data-model.md alone without checking this ADR would miss the column's existence and rationale.

## Alternatives Considered

- **`submission_scope` alone, as data-model.md §9 originally specified** — rejected: satisfies "one approval, one submission event" but not "one approval, one content state of that event." A caller could reuse the same `submission_scope` value (e.g. a stable identifier like "final-submission") across multiple regenerations of the application materials, and the stored approval would look valid for all of them under the letter of FR-APP-3 while violating its intent.
- **Fold content-identity into `submission_scope` itself** (e.g. require callers to construct `submission_scope` as `f"{event_id}:{content_hash}"`) — rejected: conflates two independent concerns into one opaque string, makes "which submission event" unqueryable without also parsing out a hash, and gives up a structured column a future gate check could index or compare against directly.
- **Decide and hard-code the fingerprint algorithm now** — rejected for this slice: the only consumer of the value (`submit_prep`'s final gate) does not exist yet (T123), and guessing the algorithm ahead of that workflow's actual serialization/versioning approach risked picking one that doesn't fit what T123 assembles. Left as an explicit open decision instead of a premature one.

## References

- Feature Spec: `specs/001-scholarship-mvp/spec.md` (US6, FR-APP-3, FR-APP-4, SC-006)
- Data Model: `specs/001-scholarship-mvp/data-model.md` §9 (Submission Approval)
- Constitution: `.specify/memory/constitution.md`, Principle III (Human Approval Before Any Live External Submission)
- Related ADRs: [ADR-0001](0001-source-registry-governance-and-seed-sources.md), [ADR-0002](0002-ingestion-dedup-merge-does-not-reconcile-scalar-columns.md), [ADR-0003](0003-lexical-grounding-fails-safe-toward-false-rejection.md)
- Related code: `backend/app/models/application.py` (`SubmissionApproval`), `backend/app/data/repositories/application_repo.py` (`create_submission_approval`, `list_submission_approvals`), `backend/migrations/versions/ad4826bd57e6_create_application_planning_tables.py`
- Not yet built (referenced as future consumers): `backend/app/workflows/submit_prep/graph.py` (T123), `POST /applications/{id}/submission-approvals` (T127)
