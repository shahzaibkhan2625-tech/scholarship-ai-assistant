# Phase 4B Design — T121 (readiness), T122 (app_plan), T126 (application endpoints)

Status: **DESIGN ONLY — awaiting approval.** No code has been written from this document.

Scope lock: T121, T122, T126 only. Not in scope: T114, T115, T116, T123, T124, T127, T128, T129.

---

## A1. Readiness decision table (ordered, first match wins)

Per checklist item, the pure function is given: `requirement` (category, key, value_status,
confidence, mandatory), `material` (resolved `DocumentType` or `None`, via A2), `material_class`
(`system_generatable` / `user_held` / `third_party_issued` / `None`), whether an
`application_documents` row for this application satisfies/matches it, whether a
`generated_documents` draft exists, and (for LANGUAGE/TEST categories) the matching
`TestScore.status`.

No LLM, no network, no DB access inside the pure function itself — all of the above are
gathered by a thin service wrapper before the pure function is called.

| # | Condition | Label | Source |
|---|---|---|---|
| 1 | `requirement.value_status in {not_applicable, conditional}` | `NEEDS_HUMAN_REVIEW` | proposed (spec silent) — data-model.md §1 defines these value_statuses but not a label mapping. Both cases mean "a human must judge whether/how this applies," never an invented applicability guess (constitution Principle I / FR-INTEL-3). |
| 2 | `requirement.value_status in {unknown, conflicting}` OR `requirement.confidence != "verified"` | `NEEDS_OFFICIAL_VERIFICATION` | proposed (spec silent), grounded in data-model.md §1 value-status vocabulary + spec.md Edge Cases ("What happens when a previously 'verified' scholarship record becomes stale... re-verification is attempted before facts from it are presented as current") + FR-VERIFY-5 (verified/unverified/stale lifecycle). |
| 3 | An `application_documents` row for this application has `satisfies_requirement_id == requirement.id` | `COMPLETE` | data-model.md §6 (`satisfies_requirement_id`) + spec.md US5 Acceptance Scenario 1 ("the transcript requirement is marked satisfied") + US6 Acceptance Scenario 1 (`Complete` label). Checked independently of `material` resolution, so a requirement in an otherwise-unmapped category that is genuinely linked to a document still resolves `COMPLETE` rather than falling to the catch-all. |
| 4 | `material` resolved AND an `application_documents` row for this application has `type == material` (but did not match rule 3) | `NEEDS_HUMAN_REVIEW` | proposed (spec silent) — a document of the right type exists but its link to *this specific* requirement is not confirmed. Per `requirement_satisfaction.py`'s existing discipline, "unknown" must never collapse into a guessed pass or fail, so this is routed to a human rather than assumed either way. |
| 5 | `material_class == system_generatable` AND a matching `generated_documents` row exists (mapping in A2) | `NEEDS_HUMAN_REVIEW` | spec.md FR-GEN-2 / SC-004 + data-model.md §7 (`ground_check` gate). A generated draft existing is not itself `COMPLETE` — a human must still review it. |
| 6 | `material_class == system_generatable` (no draft found) | `AI_CAN_GENERATE` | spec.md US6 Acceptance Scenario 2 ("distinguishing what the student must obtain from what the system can generate") + FR-PLAN-2. |
| 7 | `material_class == user_held` | `MISSING` | data-model.md §8 `Task` docstring ("`MISSING` (absent, still obtainable by the user or the system)"). The `user_held` / `third_party_issued` class taxonomy itself is proposed (spec silent). |
| 8a | `material_class == third_party_issued` AND `category in {LANGUAGE, TEST}` AND a matching `TestScore.status == "have"` | `MISSING` | Directly implements the task prompt's instruction to "Use `test_scores.status` (have/planned/none) where it applies" — the student already holds the score, they just haven't uploaded it. |
| 8b | `material_class == third_party_issued` (else) | `USER_MUST_OBTAIN` | data-model.md §8 `Task` docstring ("`USER_MUST_OBTAIN`... the system can never generate this, e.g. an official transcript"). |
| 9 | catch-all: `material` unresolved (category/key not mapped) | `NEEDS_HUMAN_REVIEW` | Task prompt's own proposed row 8 + the "NO DROPPING" hard property — an unmapped item still appears on the checklist rather than being silently omitted. |

### Hard properties verified against this table

- **Totality**: `value_status` has exactly 5 possible values in the data-model.md §1 vocabulary
  (`known, unknown, not_applicable, conditional, conflicting`). Rows 1–2 dispose of
  `not_applicable`, `conditional`, `unknown`, `conflicting`, and any `known` value paired with
  non-`verified` confidence. The only remaining case — `known` + `verified` — falls through to
  rows 3–9, which are exhaustive over `material_class ∈ {system_generatable, user_held,
  third_party_issued, None}` crossed with "document exists / doesn't." Every path terminates in
  exactly one label; none returns `None`, raises, or falls through un-labeled.
- **No dropping**: row 9 guarantees an unmapped category/key still produces a labeled item.
- **unknown ≠ not_satisfied**: rule 4 never asserts failure for an unresolved document link — it
  routes to `NEEDS_HUMAN_REVIEW`, distinct from any (unimplemented) "confirmed not satisfied"
  path.
- **MISSING vs USER_MUST_OBTAIN decided by data**: driven by `material_class` (rows 7/8) and, for
  LANGUAGE/TEST, by `TestScore.status` (rule 8a vs 8b) — never by wording/description text.
- **NEEDS_HUMAN_REVIEW vs NEEDS_OFFICIAL_VERIFICATION never swapped**: rule 2 (official
  verification) fires only on the scholarship-side `value_status`/`confidence` of the
  *requirement itself*; rules 1, 4, 5, 9 (human review) fire only on document/draft/applicability
  states a person must look at. The two conditions are checked on disjoint inputs and never
  overlap.

### Departures from the proposed table in the original prompt

1. Split the proposed rule 1 into table rows 1 and 2. The original rule 1 would have routed
   `not_applicable` into "needs official verification," which is incorrect — nothing needs
   verifying if a requirement doesn't apply at all. `not_applicable` and `conditional` (both
   "needs a human judgment call on applicability") are now row 1 (`NEEDS_HUMAN_REVIEW`); only
   `unknown`/`conflicting`/unverified-confidence are row 2 (`NEEDS_OFFICIAL_VERIFICATION`).
2. Row 3 (`COMPLETE` via `satisfies_requirement_id`) is evaluated independently of whether
   `material` resolves at all, and before row 4. This ensures a requirement in an unmapped
   category that is nonetheless linked to a satisfying document still resolves `COMPLETE`
   instead of incorrectly falling through to the row-9 catch-all.

---

## A2. Requirement → material → material class mapping

**Primary lookup**: `requirement.key`, matched case-insensitively against `DocumentType` enum
values (scholarship-specific keys such as `"transcript"` or `"sop"` are set during
extraction/ingestion — this mirrors how a user declares `type` on upload).

**Fallback**: `requirement.category`, using the default material below, when `key` doesn't match
any `DocumentType` value.

If neither resolves, `material = None` (unmapped) and the item falls to A1 row 9.

**Revision (post-approval correction)**: material class is decided by **who produces the
document**, not by a looser "does the student already have a copy" intuition. An institution-
issued document (transcript, degree certificate, an employer's experience letter, etc.) is
`third_party_issued` — even though a student often has a personal copy on hand, obtaining a
*fresh, official* one for a specific application is the canonical `USER_MUST_OBTAIN` case (see
the `Task` docstring in `app/models/application.py`). `user_held` is reserved for material the
student personally authored or accumulated with no issuing authority to go back to (portfolio,
publications, financial statements they compiled themselves).

### By key (any category) → material class

| DocumentType (by key) | Material class |
|---|---|
| transcript, degree_certificate, certificate, work_experience_doc | `third_party_issued` |
| language_test_doc, gre_gmat_doc | `third_party_issued` (overridable to `MISSING` via rule 8a) |
| recommendation_info, character_certificate, scholarship_specific_form, university_specific_form | `third_party_issued` |
| portfolio, publication, financial_doc | `user_held` |
| cv, europass_cv, sop, motivation_letter, personal_statement, study_plan, research_proposal | `system_generatable` |
| other, unclassified | unmapped (`None`) |

### By category (fallback default, when `key` doesn't match a `DocumentType`)

| RequirementCategory | Default material | Material class | Left unmapped? |
|---|---|---|---|
| ACADEMIC | transcript | `third_party_issued` | — |
| GPA | transcript | `third_party_issued` | — |
| LANGUAGE | language_test_doc | `third_party_issued` (rule 8a may downgrade to `MISSING`) | — |
| TEST | gre_gmat_doc | `third_party_issued` (rule 8a may downgrade to `MISSING`) | — |
| EXPERIENCE | work_experience_doc | `third_party_issued` | — |
| RESEARCH | research_proposal | `system_generatable` | — |
| ELIGIBILITY | — | — | **Yes** — no document type represents a general eligibility fact; routes to A1 row 9. |
| AGE | — | — | **Yes** — age is a profile fact, not a document; routes to A1 row 9. |
| NATIONALITY | — | — | **Yes** — the Blueprint §17.2 material list mentions "passport/ID info," but no `DocumentType` enum member exists for it in `app/models/document.py`; routes to A1 row 9 unless the requirement's `key` happens to match some other `DocumentType`. |
| OTHER | — | — | **Yes** by category (the category itself is a catch-all); resolved only via a direct `key` match above, otherwise routes to A1 row 9. |

Rule 8a (LANGUAGE/TEST + `TestScore.status == "have"` → `MISSING` instead of `USER_MUST_OBTAIN`)
is unchanged by this revision — it already applied only to `language_test_doc`/`gre_gmat_doc`,
which were already `third_party_issued`. It ensures a student who already holds a score is never
wrongly told to go obtain one; they're told to upload what they have.

### Generated-draft matching (A1 rule 5)

| Material (`DocumentType`) | Matching `GeneratedDocumentType` |
|---|---|
| cv, europass_cv | `CV` |
| sop | `SOP` |
| motivation_letter | `MOTIVATION` |
| personal_statement, study_plan, research_proposal | **No reliable match** — proposed (spec silent): `generated_documents` carries no requirement/material link, and no existing workflow (`cv_gen`, `sop_gen`) produces a `GeneratedDocumentType.OTHER` row today. These three materials simply never satisfy rule 5 and fall through to rule 6 (`AI_CAN_GENERATE`) until a future generation workflow and/or a requirement-linking column changes this. |

---

## A3. Deterministic sort key

```
(not requirement.mandatory, requirement.category.value, requirement.key, str(requirement.id))
```

- Mandatory requirements (`mandatory=True`) sort before optional ones (`not True` = `False` = 0,
  sorts first).
- Then alphabetical by `category.value`, then by `key`.
- `str(requirement.id)` (UUID) is the final tiebreaker, guaranteeing a total, deterministic order
  even when two requirements share category and key — and guaranteeing the same order on every
  run regardless of Postgres's unordered row return.

Source: proposed (spec silent) — data-model.md does not define a checklist ordering; this
directly implements T122's explicit instruction not to rely on DB row order.

---

## A4. Planned file list (Phase B)

- `backend/app/services/readiness.py` (new) — pure `resolve_readiness_label` (A1 table) + a thin
  service function gathering per-application inputs via existing repositories and calling it.
- `backend/app/workflows/app_plan/__init__.py` (new)
- `backend/app/workflows/app_plan/graph.py` (new) — LangGraph workflow: load scholarship
  requirements → resolve labels via the readiness service → persist via
  `replace_tasks_for_application` → return an `ApplicationPlan`. Single public entry point
  `run_app_plan(db, user_id, application_id)`.
- `backend/app/schemas/document.py` (edit) — add `checklist: list[ChecklistItem]` to
  `ApplicationRead`, matching openapi.yaml's `Application` schema (which already includes
  `checklist`).
- `backend/app/api/application.py` (edit) — add `GET /applications` (tracker, reads persisted
  tasks, no side effects) and `POST /applications/{id}/plan` (calls `run_app_plan`).
- `backend/tests/services/test_readiness_matrix.py` (new) — parametrized truth-table test over
  every row in A1, plus a totality test exercising the catch-all.
- `backend/tests/workflows/test_app_plan.py` (new) — idempotency (run twice → identical plan, no
  duplicate tasks), deterministic ordering (A3), no-dropping (unmapped category still appears),
  every item has exactly one valid label.
- `backend/tests/api/test_application_api.py` (edit — fixture data only) — the
  `scholarship_with_requirement` fixture's `confidence="high"` is adjusted to `"verified"` (the
  real vocabulary used elsewhere in the codebase, e.g. `ingestion/graph.py`, `normalize.py`); no
  assertions are changed, per the constraint that T113's five tests must pass without their
  assertions changing.
