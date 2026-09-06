# Phase 1 Data Model: Scholarship AI Assistant — MVP Core Platform

Derived from spec.md's 9 Key Entities, expanded into concrete PostgreSQL tables per blueprint §14 (schema-level) and constitution Principle I's value-status/provenance discipline. Every user-owned table carries `user_id` from day one (constitution: SaaS isolation is a WHERE-clause guarantee later, not a migration). `users` (Phase 0, existing) is unchanged and not repeated here.

Value-status vocabulary (used throughout): `value_status ∈ {known, unknown, not_applicable, conditional, conflicting}`, `confidence ∈ {verified, inferred, unknown}`. A field with `value_status = unknown` MUST NEVER be defaulted or guessed (constitution Principle I; FR-INTEL-2/3).

---

## 1. User Profile *(spec Key Entity 1 → `profiles` + satellite tables)*

**`profiles`**
| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users.id | unique — one profile per user in MVP (spec Assumptions: single applicant persona) |
| name | text | |
| nationality | text, nullable | |
| country_of_residence | text, nullable | |
| current_degree | text, nullable | |
| target_degree_level | enum(BS, MS, PhD, other) | FR-PROFILE-2 |
| target_fields | text[] | |
| target_countries | text[] | |
| updated_at | timestamptz | living record — FR-PROFILE-1 |

**`education_records`**: id, profile_id FK, degree, field, university, gpa, gpa_scale, start, end.

**`test_scores`**: id, profile_id FK, test_type enum(IELTS, TOEFL, PTE, GRE, GMAT, other), status enum(have, planned, none), score nullable, taken_at nullable.

**`experience`**: id, profile_id FK, kind enum(work, research, publication, project), title, org, detail, start, end.

**`profile_criteria`** *(drives matching — FR-PROFILE-3/4, FR-MATCH-1)*
| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| profile_id | FK | |
| dimension | enum(degree, funding, field, country, nationality, language, test, gpa, spouse_dependent, other) | |
| operator | text (e.g. `=`, `>=`, `in`) | |
| value | jsonb | |
| kind | enum(hard_constraint, soft_preference, exclusion) | **explicit student classification** — FR-PROFILE-3 |
| weight | numeric, nullable | soft-preference weighting only |
| note | text, nullable | |

**`missing_info`**: id, profile_id FK, dimension, reason. *Tracks fields the student hasn't provided — distinct from a `profile_criteria` row; never silently omitted (FR-PROFILE-3, Edge Cases).*

**Validation rules**: `target_degree_level` required at profile creation is NOT enforced (a profile can be partial — US1 Acceptance Scenario 3: unanswered fields are `missing_info`, not blocked). Reclassifying a `profile_criteria.kind` (US1 Scenario 4) is an update, never a new row, so matching always reads the current classification.

**State transitions**: none (CRUD only; "living profile" = continuously updatable, not versioned/workflowed in MVP).

---

## 2. Scholarship Record *(spec Key Entity 2 → `scholarships` + satellite tables)*

**`universities`**: id, name, country.
**`programs`**: id, university_id FK, name, field, duration, intake.

**`scholarships`**
| Field | Type | Notes |
|---|---|---|
| id | UUID PK | |
| name | text | |
| provider | text | |
| country | text, nullable | |
| university_id | FK, nullable | |
| program_id | FK, nullable | |
| field | text, nullable | |
| degree_level | enum(BS, MS, PhD, other), nullable | |
| funding_status | enum(fully_funded, substantially_funded, partially_funded, tuition_only, stipend_only, other_combination, unknown) | FR-INTEL-5 — **never a binary flag, never a universal % threshold** |
| intake | text, nullable | |
| application_fee | text, nullable | |
| application_method | text, nullable | |
| application_procedure | text, nullable | |
| official_scholarship_url | text | |
| official_application_url | text, nullable | |
| lifecycle_status | enum(newly_discovered, verified, unverified, updated, expired, closed, reopened, stale, source_unavailable) | FR-VERIFY-5 |
| opening_date | date, nullable | |
| deadline | date, nullable | |
| closing_status | text, nullable | |
| conditions | text, nullable | |
| exceptions | text, nullable | |
| notes | text, nullable | |
| retrieved_at | timestamptz | |
| last_verified_at | timestamptz, nullable | |
| verification_status | enum(verified, unverified, conflicting) | |

**`scholarship_fields`** *(generic per-field value-status carrier — FR-INTEL-1/2/3)*: id, scholarship_id FK, key, value, value_status, confidence, source_id FK → scholarship_sources, evidence_snippet, last_verified_at. *Used for any scholarship attribute not already a first-class column (e.g. spouse/visa/insurance details before they're promoted to `funding_details` columns).*

**`requirements`**: id, scholarship_id FK, category enum(eligibility, academic, gpa, language, test, age, experience, nationality, research, other), key, value, mandatory boolean, value_status, confidence.

**`funding_details`**: id, scholarship_id FK, tuition_coverage, tuition_amount_or_pct, stipend, accommodation, travel_airfare, health_insurance, visa_support, family_dependent_benefits, spouse_allowed, dependent_policy, value_status, confidence.

**`scholarship_sources`** *(per-scholarship provenance — FR-INTEL-4, FR-VERIFY-4)*: id, scholarship_id FK, source_id FK → source_registry, url, retrieved_at, verified_at, verification_status enum(verified, unverified, conflicting), reliability, evidence_snippet.

**Validation rules**: no field is assumed present (constitution Principle I) — absence of a row/column value is `value_status = unknown`, not empty-string. A `deadline` in the past MUST surface `lifecycle_status = expired` or `closed`, never presented as open (Edge Cases).

**State transitions**: `lifecycle_status` transitions are one-directional in normal flow (`newly_discovered → verified → ... `) but MAY move to `stale` from any post-verification state on freshness-window expiry, and MAY move `closed → reopened` (FR-VERIFY-5). No transition is enforced by a DB constraint in MVP; it's set by the `verification`/`conflict_resolution` services (Phase 2).

---

## 3. Source Registry Entry *(spec Key Entity 3 → `source_registry` + `candidate_sources` + `source_fetch_log`)*

**`source_registry`**: id, name, organization, country, region, source_type enum(gov, national_education, university, department, provider, foundation, ngo, embassy, international_org, research, api, approved_aggregator), official_status, domain, access_method enum(api, mcp, web), discovery_role, verification_role, reliability_level, update_frequency, status enum(active, pending, disabled), extraction_rules jsonb, constraints jsonb, last_checked_at, last_success_at, notes.

**`candidate_sources`** *(controlled expansion — FR-DISC-5)*: id, discovered_from, url, proposed_type, signals jsonb, status enum(pending, approved, rejected), reviewed_by FK → users.id nullable, reviewed_at nullable.

**`source_fetch_log`** *(coverage + health + retry — FR-COV-1, FR-VERIFY-6)*: id, source_id FK, started_at, status enum(ok, fail, timeout), http_status, error, retry_count, items_found.

**Validation rules**: a connector MUST reject any fetch whose `source_id` is not `status = active` (constitution Principle IV) — enforced in the `sources/` connector layer, not just the DB. A row in `candidate_sources` MUST NOT be joined into any authoritative scholarship data path until `status = approved`.

**State transitions**: `candidate_sources.status`: `pending → approved | rejected` (human-gated, `source_validate` workflow, Phase 2). `source_registry.status`: `active ⇄ disabled` (`failing` health state tracked via `source_fetch_log`, promoted to `disabled` per Phase 5 policy — not enforced in MVP beyond logging).

---

## 4. Matching Verdict *(spec Key Entity 4 → `matches`, ephemeral schema in §18)*

**`matches`**: id, user_id FK, scholarship_id FK, eligibility_verdict enum(eligible, likely, possibly, not, unknown_requires_verification), match_strength enum(strong, possible, not), hard_constraints jsonb (`[{criterion, result: pass|fail|unknown, evidence}]`), soft_preferences jsonb (`[{criterion, result: met|unmet}]`), exclusions_triggered jsonb, matched_criteria jsonb, failed_criteria jsonb, missing_information jsonb, unverified_criteria jsonb, required_documents jsonb, remaining_actions jsonb, evidence jsonb, created_at.

**Validation rules** (constitution Principle II, FR-MATCH-2/3/4/5/6): `eligibility_verdict = not` whenever any `hard_constraints[].result = fail`, regardless of `soft_preferences` strength — enforced by the deterministic `hard_constraints` service + Matching-agent output guardrail, not by DB constraint alone (a CHECK constraint alone can't express "computed by which code path", so this is a code-level, test-matrix-verified invariant — see Testing Strategy in plan.md). Every entry in `matched_criteria`/`failed_criteria` MUST carry an `evidence` reference or the record is rejected before persistence (evidence-required guardrail).

**State transitions**: none — a `matches` row is an immutable point-in-time verdict; re-matching produces a new row (history preserved for the tracker).

---

## 5. Q&A Answer *(spec Key Entity 5 → not persisted as its own table in MVP; response schema only)*

Q&A answers are stateless structured responses (blueprint §34), not stored entities in MVP (no "conversation history" requirement in scope). Schema (`schemas/qa.py`, Pydantic): `answer_text`, `confidence ∈ {verified, inferred, unknown}`, `source_url`, `source_id`, `last_verified_at`, `evidence_snippet`. *If a future phase requires persisted Q&A history, add a `qa_log` table — not needed for this spec's acceptance criteria.*

---

## 6. Uploaded Document *(spec Key Entity 6 → `application_documents`)*

**`application_documents`**: id, user_id FK, application_id FK nullable (a document may be profile-level before an application exists), type enum(transcript, degree_certificate, cv, europass_cv, sop, motivation_letter, personal_statement, study_plan, research_proposal, recommendation_info, certificate, language_test_doc, gre_gmat_doc, portfolio, publication, work_experience_doc, character_certificate, financial_doc, scholarship_specific_form, university_specific_form, other), file_ref (object-storage key), parsed_meta jsonb, satisfies_requirement_id FK → requirements.id nullable, inconsistency_flags jsonb nullable, uploaded_at.

**Validation rules**: `type` set is scholarship-required-materials-driven (FR-DOC-2/3) — the API layer only accepts a `type` the target scholarship's `requirements` actually calls for. A parse failure MUST set `parsed_meta = null` + surface an explicit error to the user (Edge Cases) — never silently ignored, never a row left in an ambiguous state.

**State transitions**: implicit via presence of `parsed_meta` (unparsed → parsed) and `satisfies_requirement_id` (unassociated → associated).

---

## 7. Generated Material *(spec Key Entity 7 → `generated_documents`)*

**`generated_documents`**: id, user_id FK, application_id FK, type enum(cv, sop, motivation, other), file_ref, source_trace jsonb (`[{claim, profile_field_or_document_id}]` — every factual claim's grounding, FR-GEN-2/SC-004), generated_at.

**Validation rules**: `source_trace` MUST cover 100% of factual claims in the generated content (SC-004) — enforced by the `ground_check` LangGraph node before a `generated_documents` row is written; a claim with no traceable source blocks generation and is reported as a gap (US5 Scenario 5), not silently omitted.

---

## 8. Application Plan *(spec Key Entity 8 → `applications` + `tasks`)*

**`applications`**: id, user_id FK, scholarship_id FK, status text, created_at.

**`tasks`** *(the checklist — FR-PLAN-2)*: id, application_id FK, description, category, readiness_label enum(complete, missing, user_must_obtain, ai_can_generate, needs_human_review, needs_official_verification), due_date nullable.

**Validation rules**: every `tasks` row MUST carry exactly one `readiness_label` (FR-PLAN-2, SC-005) — non-nullable enum column, no "none" state. Agentic-mode and deterministic-mode paths (US6 Scenario 5) both write through the same `app_plan` workflow, so they are guaranteed to produce identical `tasks` rows for the same inputs — this is a code-sharing invariant (single workflow, two entry points), not a data-model rule per se, but is testable by comparing `tasks` output for the same `(application_id)` across both entry points.

**State transitions**: `readiness_label` changes as documents/generation complete (`missing → ai_can_generate → complete`, etc.) — recomputed by the `readiness` service, not hand-edited.

---

## 9. Submission Approval *(spec Key Entity 9 → `submission_approvals`)*

**`submission_approvals`**: id, application_id FK, submission_scope text (identifies exactly which submission event/attempt this approval covers — e.g. a hash or sequence number, never a blanket "approved"), approved_by FK → users.id, approved_at, notes nullable.

**Validation rules** (constitution Principle III, FR-APP-3): a row here authorizes exactly one submission event. **No code path may treat an existing `submission_approvals` row as covering any submission other than the one identified by its `submission_scope`.** Since MVP has no live-send endpoint at all (FR-APP-4), this table's only Phase-4 consumer is the `submit_prep` workflow's final gate check — it exists now so the schema doesn't need to change when Phase 6 adds live sending.

**State transitions**: append-only — an approval is never edited or reused; a new submission attempt requires a new row.

---

## Cross-cutting notes

- **`audit_log`** (id, user_id, action, entity, at): present per blueprint §14 for basic traceability; not elaborated further here as no functional requirement in this spec calls for audit-log querying beyond existence.
- **Isolation**: every table above that is user-owned carries `user_id` (directly, or transitively via `profile_id`/`application_id`) so a repository-layer `WHERE user_id = :current_user` is always sufficient (constitution §19, FR-AUTH-2).
- **Migrations**: each phase adds its own Alembic migration(s) on top of the existing `fed0167c65c9_create_users_table` — Phase 1 migrations cover `profiles` through `matches` (sections 1, 2 minus registry tables, 4); Phase 2 adds `source_registry`/`candidate_sources`/`source_fetch_log`/`scholarship_sources`/`universities`/`programs`; Phase 3 adds `application_documents`/`generated_documents`; Phase 4 adds `applications`/`tasks`/`submission_approvals`.
