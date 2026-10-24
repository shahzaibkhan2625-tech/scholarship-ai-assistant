# Blueprint & PRD Gap Analysis

| | |
|---|---|
| Date | 2026-10-25 |
| Repo state | `main` @ `4dade20` (CI green), Alembic head `f2b8d4a6c1e3` |
| Compared against | `specs/scholarship-ai-assistant-master-blueprint-v1.md` (v1.6, "BP"), `specs/scholarship-ai-assistant-prd-v1.md` (v1 FINAL, "PRD"), `specs/charter.md`, `specs/001-*` and `specs/002-*` (spec, tasks), ADR-0001..0011, `docs/operations-hardening.md` §11 |
| Type | Read-only analysis. No code changed. No Gemini calls made. |
| Owner decisions | Recorded 2026-10-25 in §7, ADR-0012 (free tier, build order, source approval) and ADR-0013 (meaning of "verified"). Roadmap in §6 reflects them. |

## 0. How this was produced, and what to trust

- I read the Blueprint, PRD, charter, both specs, both task files and all ADRs in full (ADR "Decision / Consequences / Known gaps" sections for 0007-0011), plus the known-limitations table.
- Code evidence comes from two read-only code audits (inventory and goal-by-goal), then I spot-checked the findings that drive the conclusions. **Re-verified by me:** the `official_source_confirmed` / `VERIFIED` path, ISO-code vs free-text country matching, the orchestrator not being mounted, the missing role gate on source approval, the `fetch_url` mock failure, and the live-data counts in §1.3. One audit claim was wrong and is corrected here: `VERIFIED` *is* reachable, via `mark_confirmed_by_monitoring` (ADR-0009), see M-20.
- **Not re-verified by me** (taken from the audits, marked "per audit" where it matters): exact line numbers outside the spot-checks, the absence of refresh-token/password-reset routes (inferred from the route list), whether Q&A has any live-fetch escalation, and which coverage fields `/coverage` returns.
- **`tasks.md` checkboxes are not evidence.** `specs/001-*/tasks.md` shows 71 of 140 boxes unticked although the corresponding code and tests exist (for example T140 is unticked and has a regression test). `specs/002-*/tasks.md` has 110/110 ticked. I judged status from code and tests only.
- Estimates are marked **(E)**. Anything without (E) is measured, configured, or read from code.

**Status legend.** *Built*: implemented and tested. *Partial*: implemented but narrower than the requirement. *Missing*: not implemented and not explicitly deferred. *Deferred-by-decision*: a spec, ADR, task, or a Blueprint phase/tier label explicitly puts it later; the citation is given.

---

## 1. Executive summary

### 1.1 Counts by status (77 matrix rows, §3)

| Status | Rows | Share |
|---|---|---|
| Built | 35 | 45% |
| Partial | 26 | 34% |
| Missing | 5 | 6% |
| Deferred-by-decision | 11 | 14% |
| **Total** | **77** | |

(Counts updated 2026-10-25: M-10 moved from *Deferred* to *Missing* because ADR-0012 supersedes the Blueprint deferral and schedules it as feature 006.)

The small *Missing* count is misleading on its own: most of the real distance to the product vision sits in the 26 *Partial* rows, where a capability exists but is narrower than the requirement (one listing page, four hard-constraint dimensions, five sources). The five *Missing* rows are large-scale source expansion (M-10, now scheduled), monitoring cost control (M-36), natural-language constraint parsing (M-49), live-fetch escalation in Q&A (M-51), and a draft review loop (M-63). Of the 11 *Deferred* rows, only the frontend (M-67) and reminders (M-38) materially block the stated product goals.

### 1.2 Top 5 gaps

1. **Stored scholarships carry almost no matchable data, so matching cannot work on discovered records.** Live DB: 34 scholarships, **34/34 `deadline` NULL, 0 requirement rows**, 8 NULL country. Discovery and monitoring extract only name/provider/country/field/degree/funding/deadline from one listing page and never follow `detail_url`. Hard constraints for GPA, nationality, tests and deadline therefore return `unknown`, and most verdicts are `unknown_requires_verification` (M-17, M-28, M-41, M-46).
2. **The Gemini free tier is already over-subscribed by monitoring alone.** 2 monitored sources x 24 runs/day = **~48 extraction calls/day (E)** against an observed cap of 20/day. `bypass_cache=True` on every run, no content-hash skip, no per-source cadence, no budget guard. A real 429 was logged in production on 2026-10-24 (M-36).
3. **Source coverage is thin, non-hierarchical and not generic.** 5 seeded sources (2 monitored), 5 countries, one university, no regional/state/institution levels in the schema. Discovery's country filter is an exact match against ISO seed codes (`DE`, `PK`) while users type free text, so "Germany" matches no source. New-source discovery only follows outward links from already-registered pages (≤5 per fetch, English keywords). Any logged-in user can approve a source (M-08, M-09, M-12, M-14).
4. **"Verified" is weaker than the Blueprint's promise.** No call site ever passes `official_source_confirmed=True`. A record becomes `VERIFIED` when monitoring re-sights it on an approved listing (ADR-0009), not when its own official page is opened. 34/34 are `VERIFIED` while 34/34 lack a deadline (M-20).
5. **No end-to-end user path.** No frontend (explicitly deferred, M-67), the orchestrator is built but mounted nowhere (M-48), discovery returns unranked ids that are not matched to the profile (M-13), there is no one-call "match me against everything" (M-45), and the production DB has 0 users, 0 profiles, 0 matches, 0 applications.

### 1.3 Live data snapshot (read-only queries, 2026-10-25)

| Measure | Value |
|---|---|
| `scholarships` | 34, all `verification_status=VERIFIED` (33 lifecycle `VERIFIED`, 1 `UPDATED`) |
| Scholarships with a deadline | **0 of 34** |
| Scholarships with ≥1 `requirements` row | **0 of 34** |
| Country values | Pakistan 10, NULL 8, Afghanistan 2, `USA` 1 and `usa` 1 (not normalised), plus multi-valued strings ("Pakistan, Sri Lanka", "China, Pakistan", ...) |
| `source_registry` | 5 (4 `ACTIVE`, 1 `FAILING`: EACEA, 136 of 136 recent extractions yielded 0 grounded candidates) |
| `candidate_sources` | 2 `PENDING` |
| Users / profiles / matches / applications | 0 / 0 / 0 / 0 |
| Monitored sources (`discovery_role` and `web`) | 2 (EACEA, HEC) |

### 1.4 Proposed first feature

**`003-quota-governance-and-cheap-monitoring`**: a central LLM budget guard, content-hash skip, ETag, per-source cadence driven by `update_frequency`, and 429 back-off. It cuts monitoring from ~48 to ~2 calls/day (E) and is the precondition for every later feature, because nothing else fits the free tier while monitoring consumes 2.4x the daily allowance (§5, §6).

---

## 2. What is built (compact inventory)

~13,950 lines of Python in `backend/app`, ~761 test functions, 27 tables, Alembic head `f2b8d4a6c1e3`. File:line references are per the code audit.

- **Agents (4 of the Blueprint's 5 are real):** discovery (`agents/discovery/agent.py:114,289`), matching (`agents/matching/agent.py:92,135`), research/Q&A (`agents/research_qa/agent.py:28`), application (`agents/application/agent.py:52`, deterministic rule table, no LLM). The 5th, the main orchestrator (`orchestration/main_agent.py:104,231`), is a keyword/rule router referenced only by `tests/integration/test_main_agent_routing.py`.
- **Workflows:** ingestion, url_match, doc_pipeline, app_plan, submit_prep, cv_gen, sop_gen, source_validate (all LangGraph); source_monitor is plain Python (`workflows/source_monitor/graph.py:542`). `source_validate` is referenced only by tests.
- **Connectors/tools:** `official_fetch` (`sources/connectors/official_fetch.py:93,148,312`), `api_connector` (no provider onboarded), `web_fetch`, `extract_listing`, `extract_requirements`, `extract_document_fields`, `extract_profile`, `classify`, `ground_check`, `detect_candidate_links`, `pdf_parse`, `embed`, `rerank` (off). `search_tool` raises `NotImplementedError` (`tools/search.py:40`) and nothing calls it.
- **API:** 36 routes (auth, profile, url-match, matching, Q&A, discovery, sources/candidates, health, coverage, monitoring, alerts, applications, documents, generation, metrics). No frontend, no orchestrator route.
- **Tables:** identity/profile (7), scholarship data (6), sources/monitoring (6), matching/alerts (3), applications (5).
- **Seed sources (5, all `web`):** DAAD (DE), EACEA (EU), Stipendium Hungaricum (HU), HEC (PK), KAUST (SA). Only EACEA and HEC have `discovery_role=true`.
- **Scheduler:** `source_monitor` every 60 min (in-memory store), `fetch_log_retention` daily 03:00 UTC (`scheduling/scheduler.py:59,67`).
- **Evals:** three scored cases (matching_correctness 1.0, qa_groundedness, generation_groundedness), CI-gated (`evals/runner.py:71`).
- **Ops:** HTTPS via Caddy, rate limit, metrics, retention, Neon PITR drill passed 2026-10-24, Oracle VM deployment (`docs/operations-hardening.md`).

---

## 3. Capability matrix

Citations: BP = Blueprint section, PRD IDs as written in the PRD. Evidence is `path:line` or a test name.

### A. Accounts and profile

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-01 | Sign up / login / logout, JWT (FR-AUTH-1; BP §20) | **Built** | `api/auth.py:12,30,49`; `tests/test_auth.py` | None for the MVP |
| M-02 | Refresh tokens, password reset (BP §2, Auth row, Production tier) | **Deferred-by-decision** | BP §2 table | No such routes exist; acceptable for a single-user tool |
| M-03 | Per-user data isolation (FR-AUTH-2; NFR-SEC-1/3; BP §19) | **Built** | `user_id`-scoped repositories; Phase 4 T132 isolation check (002 spec NFR-HARDEN-8); 112 API tests | None known |
| M-04 | Living profile with the PRD field set (FR-PROFILE-1/2; BP §14) | **Partial** | `models/profile.py`; `api/profile.py:27,36` | No age, spoken languages, finances/need or dependants fields; funding/visa/spouse preferences only as free-form criteria |
| M-05 | Hard / soft / exclusion criteria and missing-info tracking (FR-PROFILE-3; BP §14) | **Built** | `profile_criteria.kind`, `missing_info`; `POST /profile/criteria` `api/profile.py:53` | None |
| M-06 | CV import as suggestions only (BP §2 Profile) | **Built** | `api/profile.py:81`; `tools/extract_profile.py` | 1 LLM call per import |

### B. Sources, discovery and verification

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-07 | Governed Source Registry; a candidate is never authoritative (FR-DISC-3/5; FR-VERIFY-2; BP §29) | **Built** | `models/source.py:80,121`; ADR-0001 (separate tables + status filter); `tests/sources/test_source_registry_compliance.py` | None |
| M-08 | Human-only approval gate for new sources (BP §7.0, §29; FR-CANDVAL-3) | **Partial** | `api/sources.py:51-95` depends only on `get_current_user`; `models/user.py` has no role column | Any logged-in user can approve or reject a source; no operator role. **Decision 2026-10-25 (ADR-0012):** restrict to operators, minimal slice in feature 004, fuller roles in 006 |
| M-09 | Coverage of national, regional/state and university sources in any country (FR-DISC-1; BP §29, §30) | **Partial** | `models/source.py:30-90` (12 types; free-text `country`/`region`); `seed_sources.yaml` (5 rows) | No regional/state level or institution hierarchy; one university; 5 countries; `region` holds continents |
| M-10 | Large-scale source/country expansion (BP §22 Phase 6, §36) | **Missing** | BP §36 labelled it "Future/Advanced (Phase 6)" and BP A11 seeds a small registry; **superseded by ADR-0012 (2026-10-25)**, which schedules it as feature 006, built after 003 and 004 | Not built; conflicts with product goal (a) until 006 ships |
| M-11 | Multi-channel retrieval: APIs, MCP, approved search, aggregators as signal (FR-DISC-1; BP §30.1) | **Partial** | `api_connector.py` has no provider (ADR-0001 #3); `AccessMethod.MCP` has no implementation; `tools/search.py:40` `NotImplementedError` | Only registry-gated web listing pages work |
| M-12 | Systematic query plan across dimensions (FR-DISC-6; BP §30.3) | **Partial** | `agents/discovery/agent.py:114`; `source_repo.py:59` | Only country, field, degree_level, nationality (no university, funding type, intake, deadline); country filter is exact-match against ISO seed codes so "Germany" or "Pakistan" match no source on that dimension |
| M-13 | Ranked, verified, source-attributed results plus coverage (AC-2; BP §3.1) | **Partial** | `agents/discovery/agent.py:289`; `api/discovery.py:37` | Returns unranked ids; no profile match; synchronous request |
| M-14 | Flag unknown sources as pending candidates (FR-DISC-5; ADR-0005) | **Built** | `tools/detect_candidate_links.py` (`_KEYWORDS` line 76, cap line 40); `official_fetch.py:285` | Low recall by design: English keywords, ≤5 per fetch, only outward links from already-registered pages |
| M-15 | Semi-automated candidate validation (FR-CANDVAL; BP §36 Production) | **Built** | `services/candidate_validation.py`; `source_monitor/graph.py:603`; ADR-0010 | English/`.gov`/`.edu` heuristics; 1 LLM call per candidate |
| M-16 | `source_validate` workflow with human gate (BP §10) | **Partial** | `workflows/source_validate/graph.py:80` | Referenced only by tests; the live approval path is a separate endpoint |
| M-17 | Extract candidates from listing pages (BP §31 EXTRACT; T135) | **Partial** | `tools/extract_listing.py:110,119`; `official_fetch.py:312` | One page per source, no pagination; `detail_url` extracted but never followed; EACEA yields 0 grounded candidates (136/136 recent attempts) |
| M-18 | Normalise and classify (BP §31) | **Partial** | `tools/classify.py:66-78,140` | 32 English country names and 20 fields, substring match ("uk" inside longer words); LLM fallback failure swallowed; live data: 8 NULL country, `usa` vs `USA`, multi-valued country strings |
| M-19 | Deduplicate (BP §31; ADR-0002) | **Built** | `services/dedup.py`; `tests/workflows/test_ingestion.py` | Scalar columns not reconciled on merge (ADR-0002, accepted) |
| M-20 | Verify against the official source (FR-VERIFY-1/3; BP §32) | **Partial** | `grep official_source_confirmed=True app/` returns nothing; `scholarship_repo.py:91` `mark_confirmed_by_monitoring`; `source_monitor/graph.py:315`; ADR-0009 | `VERIFIED` means "re-sighted on an approved listing", not "confirmed on the scholarship's own official page"; no step opens that page for discovered records. **Decision 2026-10-25 (ADR-0013):** `verified` requires the scholarship's own official page with a deadline and eligibility present; listing-only records become `listed`; the 34 current rows are reclassified in feature 004 |
| M-21 | Conflict resolution: official > recent > reliable (FR-VERIFY-7) | **Built** | `services/conflict_resolution.py`; ADR-0002 | None |
| M-22 | Provenance on every record (FR-VERIFY-4; FR-INTEL-4) | **Built** | `models/source.py:185` (`scholarship_sources`); `scholarship_fields` | None |
| M-23 | Failed fetch is never "none found"; retry/backoff (FR-VERIFY-6; NFR-REL-2; BP §33) | **Built** | `sources/retry_policy.py:14-16`; `source_fetch_log`; `tests/sources` | Provider (LLM) errors inside discovery are only logged, not recorded in `source_fetch_log` |
| M-24 | Measured coverage, never 100% (FR-COV-1; FR-DISC-7; BP §30.4) | **Built** | `api/coverage.py:18`; `services/coverage.py` | Honest but tiny, because the registry is |
| M-25 | Source health, failing flag (FR-HEALTH; 002 US3) | **Built** | `api/health.py:22,43`; ADR-0008 | None |
| M-26 | Health recovery for API-access sources | **Deferred-by-decision** | ADR-0008 "Known gaps"; 002 tasks note | Moot until an API source exists |
| M-27 | Coverage analytics dashboards (BP §36 Production) | **Deferred-by-decision** | BP §36; needs the frontend (M-67) | The API exists (M-24); no dashboard |

### C. Scholarship records and freshness

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-28 | Comprehensive scholarship schema (FR-INTEL-1; BP §14) | **Partial** | `models/scholarship.py`; live DB: 34 rows, 0 requirement rows, 34 NULL deadlines | `requirements`, `funding_details`, `universities`, `programs` exist but discovery/monitoring never populate them |
| M-29 | Per-field value-status and confidence (FR-INTEL-2/3) | **Built** | `scholarship_fields`; `ValueStatus` | None |
| M-30 | Funding classification, no binary flag (FR-INTEL-5; OD-6) | **Built** | seven-value `funding_status` (`tools/extract_listing.py` prompt) | None |
| M-31 | Nine lifecycle states, deterministic transitions (FR-VERIFY-5; FR-LIFECYCLE) | **Built** | `services/lifecycle.py`; ADR-0009; lifecycle walk integration test | None |
| M-32 | `expired` driven by the deadline passing | **Deferred-by-decision** | ADR-0009 "Known gaps" | Moot while every deadline is NULL |
| M-33 | Freshness windows configurable per source (NFR-REL-1; OD-4) | **Built** | `freshness_window_days`, `update_frequency`; `services/verification.py:17` (180 d default) | Not used to schedule checks (M-36) |
| M-34 | Scheduled monitoring, deterministic, not an agent (FR-MON-1..5; NFR-REL-3; BP §35) | **Built** | `scheduling/scheduler.py:59`; `source_monitor/graph.py:542` | Selects only `discovery_role` + `web` sources (2 of 5) |
| M-35 | Detect new / changed / closed / reopened / stale (FR-MON-3) | **Partial** | `services/listing_diff.py`; ADR-0006 | Only deadline, funding, degree level; requirement changes cannot be detected (ADR-0006); no content hash, so every run re-extracts with the LLM |
| M-36 | Monitoring cost and cadence fit the free tier (NFR-REL-1; operational) | **Missing** | `source_monitor/graph.py:447` `bypass_cache=True`; `scheduler.py:59` 60 min | ~48 LLM calls/day (E) vs 20/day observed cap; no budget guard, hash skip, ETag, or per-source cadence; `core/llm.py` has no retry or quota guard |
| M-37 | Re-match and alert owners, in-app + email (FR-ALERT; OD-8) | **Built** | `services/alerts.py`; `api/alerts.py:18-48`; ADR-0007 | Closed/reopened/stale alerts out of scope (ADR-0007 D2); email carries no link |
| M-38 | Deadline-approaching reminders (BP §2 Planner row, Production tier) | **Deferred-by-decision** | BP §2 | Nothing built; also pointless until deadlines exist (M-28) |
| M-39 | Alert channels extensible (OD-8) | **Built** | `alert_preferences`; `services/alerts.py` | Only in-app and SMTP exist |
| M-40 | Fetch-history retention policy (NFR-HARDEN-7) | **Built** | `services/retention.py`; `scheduler.py:67` | None |

### D. Matching and routing

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-41 | Deterministic hard constraints, never LLM (FR-MATCH-5; BP §7.5a) | **Partial** | `services/hard_constraints.py:167,62` | Evaluators for GPA, nationality, degree, tests (+ deadline) only; country, field, funding, language, spouse/dependent return UNKNOWN; GPA ignores `gpa_scale`; nationality is an exact string ("Pakistani" != "Pakistan") |
| M-42 | Guardrailed matching agent (FR-MATCH-6; BP §7.5) | **Built** | `agents/matching/agent.py:92,135`; `tests/agents/test_matching_guardrails.py`; eval matching_correctness 1.0 | None |
| M-43 | Explainable, schema-locked verdict (FR-MATCH-2/4; BP §18) | **Partial** | `schemas/match.py`; `agents/matching/agent.py:165` | `required_documents` and `remaining_actions` are always `[]` |
| M-44 | Match a scholarship URL (FR-URL; AC-1; BP §3.2) | **Built** | `api/url_match.py:20`; `workflows/url_match/graph.py:171` | The only path that creates requirement rows |
| M-45 | "Match My Profile" against stored scholarships (BP §5.1) | **Partial** | `api/matching.py:18,32,56` | Single-scholarship match and ranking of past verdicts; no one-call batch |
| M-46 | Match discovered scholarships (product goal b) | **Partial** | discovery and monitor call `run_ingestion` without `requirements` | Discovered rows lack requirement/deadline data, so verdicts are mostly `unknown_requires_verification` |
| M-47 | Deterministic ranking (BP §7.5f) | **Built** | `services/ranking.py`; `api/matching.py:56` | Not used by discovery |
| M-48 | Intent routing and clarification (FR-ROUTE-1..3; BP §7.1) | **Partial** | `orchestration/main_agent.py:104,231`; `tests/integration/test_main_agent_routing.py` | Rule/keyword router, not imported by any app module, no API route |
| M-49 | Compound natural-language constraint parsing (BP §7.1) | **Missing** | `main_agent.py` is rules only | No extraction of "fully funded IT Master's, GRE not required" |

### E. Q&A

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-50 | Grounded Q&A with Verified/Inferred/Unknown, citations (FR-QA; AC-1; BP §7.2, §34) | **Built** | `api/qa.py:18`; `agents/research_qa/agent.py:28` | None |
| M-51 | Escalate weak grounding to a live official fetch (BP §7.2, §12 Self-RAG) | **Missing** | agent flow is retrieve, generate, ground_check, label (per audit) | Returns `unknown` instead of fetching the official page |
| M-52 | Q&A over discovered scholarships | **Partial** | `ingestion/graph.py:642` (indexing needs `raw_html`; discovery passes none) | Only url-matched pages are indexed; Qdrant Cloud also unreachable (`operations-hardening.md` §11) |
| M-53 | Intent-specific response schemas (FR-OUT-1; BP §34) | **Partial** | `schemas/qa.py`, `schemas/match.py` | No dedicated spouse/funding/eligibility/how-to-apply schemas |
| M-54 | Grounding that works for any language (NFR-2; ADR-0003) | **Partial** | `tools/ground_check.py:17` `[a-z0-9]+`; ADR-0003 | Non-Latin scripts never ground; accented words split; lexical only |

### F. Documents, generation, application

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-55 | Upload, parse, associate, satisfy, flag missing (FR-DOC-1; BP §16) | **Built** | `api/documents.py:41`; `workflows/doc_pipeline/graph.py:225` | None |
| M-56 | Requirement-aware CV (Europass), grounded (FR-GEN-1/2) | **Built** | `workflows/cv_gen/graph.py:231` | Format comes only from stored requirements, which discovered scholarships lack |
| M-57 | SOP answering the scholarship's own questions, grounded | **Built** | `workflows/sop_gen/graph.py:122` | Same dependency on requirements |
| M-58 | Other materials: motivation letter, study plan, research proposal, recommendation guidance (FR-DOC-2; BP §17.2) | **Partial** | `models/document.py:20-42` lists the types | Only CV and SOP are generated |
| M-59 | Application plan with six readiness labels (FR-PLAN; AC-4) | **Built** | `api/application.py:89`; `workflows/app_plan/graph.py:113` | None |
| M-60 | Tracker and readiness (FR-TRACK-1) | **Partial** | `api/application.py:41,55`; `services/readiness.py` | No saved-scholarship list or deadline task view; no reminders (M-38) |
| M-61 | Application assistant, step-by-step plus button parity (FR-APP-1/2; BP §7.4) | **Built** | `agents/application/agent.py:52`; `api/application.py:103` | Deterministic rule table, not LLM planning; unreachable from the orchestrator (M-48) |
| M-62 | Approval scoped and content-bound; no send path (FR-APP-3; ADR-0004) | **Built** | `services/submission_approval.py`; `workflows/submit_prep/graph.py:202`; `tests/api/test_no_live_submission.py` | None |
| M-63 | Review / approve / edit loop for generated drafts | **Missing** | `api/generation.py:48,72` | Drafts are produced; there is no approve, reject or edit state |
| M-64 | Live approval-gated submission (FR-APP-3 future; BP §22 Phase 6, §37) | **Deferred-by-decision** | PRD §30; FR-APP-4 | By design |
| M-65 | Sandboxed execution for portal interaction (BP §9.1) | **Deferred-by-decision** | BP §9.1 "NOT YET DEFINED"; tied to §37 | Mechanism undecided; only relevant at Phase 6 |
| M-66 | Interview-stage tracking (FR-TRACK-2) | **Deferred-by-decision** | PRD FR-TRACK-2 "future" | None |

### G. Platform, quality and operations

| ID | Requirement | Status | Evidence | Gap |
|---|---|---|---|---|
| M-67 | React + TypeScript frontend (BP §5; NFR-5) | **Deferred-by-decision** | `specs/001-*/plan.md:20,24`; `tasks.md:19` | No UI at all; every capability is API-only |
| M-68 | Async job pattern for long calls (BP §5 rules; A7) | **Partial** | `api/monitoring.py` returns 202; `api/discovery.py:37` is synchronous | Discovery and generation block the request |
| M-69 | Evals and a CI score gate (BP §25.1) | **Partial** | `evals/runner.py:71` (3 cases); `.github/workflows/ci.yml` | No extraction, verification or discovery evals; no checker-accuracy set (§25.1b) |
| M-70 | Test strategy (BP §25) | **Built** | ~761 test functions across 14 test directories | `tests/rag` has no tests of its own (covered through integration) |
| M-71 | Measurable KPIs (PRD §28 KPI-1..9) | **Partial** | `/coverage`, `/metrics`, evals | Extraction success rate, verification rate, freshness %, duplicate rate not exposed |
| M-72 | HTTPS, rate limit, logs/metrics, backups, retention (NFR-HARDEN) | **Built** | `Caddyfile`; `docs/operations-hardening.md` §13; PITR drill 2026-10-24 | `/metrics` readable by any account (accepted, ADR-0011 Q5) |
| M-73 | Minimal cloud runtime (BP §22 Phase 2, v1.6) | **Built** | Oracle VM, `docker-compose.podman.yml` | None |
| M-74 | Multi-tenancy enforcement, billing, quotas (BP §26; PRD §30) | **Deferred-by-decision** | PRD §30; BP §36 Phase 6 | By design |
| M-75 | Optional reranker, off by default (BP A3) | **Built** | `tools/rerank.py` | None |
| M-76 | SSRF redirect hardening | **Deferred-by-decision** | ADR-0010; ADR-0011 Q4 (accepted residual risk) | A redirect to a non-public address is requested before it is checked |
| M-77 | Eval thresholds and checker-accuracy bar (BP §25.1 "NOT YET DEFINED") | **Partial** | thresholds are set in `evals/runner.py`; no recorded decision | Thresholds exist in code but were never ratified; checker accuracy unmeasured |

---

## 4. The five product goals, judged generically

### 4.a Source coverage at every level, in any country

**State.** The schema can express twelve source types and free-text `country`/`region`, but has no jurisdiction hierarchy: nothing says "this university belongs to this state, in this country", and nothing models a ministry portal that links to hundreds of institutions. The registry holds 5 sources; 2 are monitored. `Program` and `University` tables exist and are never read or written.

**Not generic today.**
- Country filtering is an exact string match against ISO codes in the seed; user-entered `target_countries` is free text with no normalisation anywhere.
- `classify.py` knows 32 English country names and 20 fields.
- Candidate detection looks for seven English keywords; validation recognises `.gov`/`.edu`/`.ac.<cc>` patterns and English affiliation words.
- `ground_check` tokenises ASCII only, so non-Latin source text cannot ground.
- One source is one hostname with one listing URL: no pagination, no per-department pages, no sitemap, no robots.txt handling (all five seeds have `robots_checked: false`).

**How new sources are found and approved today.** Only by following outward links from pages of already-registered sources (≤5 per fetch), then up to 15 automatic validations per monitoring run, then a manual `POST /sources/candidates/{id}/approve` that requires the reviewer to hand-write the full registry entry. Approval has no role check. Governance is sound in structure (ADR-0001) but cannot scale to hundreds of sources.

**Constraint to respect.** FR-DISC-2 forbids unrestricted web search as the primary strategy, and BP A10 keeps approval human. "At scale" therefore has to mean: curated directory crawling (for example a national ministry's official institution list), bulk candidate import with deterministic pre-checks, per-source extraction rules, and a reviewer queue with roles, not an LLM deciding what is official.

### 4.b Matching any user profile

**State.** The matching agent, guardrails, verdict schema and ranking are built and well tested (M-42, M-47). The weakness is upstream and in the evaluator set:
- Hard-constraint evaluators exist for only GPA, nationality, degree, tests and deadline. Country, field, funding, language and spouse/dependent return UNKNOWN even when the profile declares them hard (`hard_constraints.py:167`).
- GPA comparison ignores scale; nationality is a raw string compare, so demonyms and country names never match.
- The profile has no age, spoken languages, financial need or dependants.
- Requirement rows exist only for url-matched pages. Discovered scholarships have none, so a stranger's profile matched against the 34 stored scholarships mostly yields `unknown_requires_verification`.
- There is no batch "match me against everything" call, and discovery does not use the profile for anything except choosing which sources to visit.
- Positive: no user- or country-specific values were found in matching logic. The generic-ness problems sit in classification and source filtering, not in the matcher.

### 4.c Application assistance through the agent, human-approved

**State.** Strong on safety, narrow on content. Built: documents, requirement satisfaction, CV and SOP generation with a deterministic grounding gate, the six-label plan, a deterministic assistant with button parity, append-only approvals bound to scope and content fingerprint (ADR-0004), and a test asserting no outbound send path exists. Missing or partial: other written materials (M-58), a review/approve/edit loop for drafts (M-63), a tracker with saved scholarships and deadlines (M-60), reminders (M-38), and the orchestrator that would let a user say "prepare my application" (M-48). All of it depends on requirement data that discovered scholarships do not have (M-28).

### 4.d Accuracy: grounding, official-source-only, verification status

**Strong.** Registry-gated fetching, schema-locked verdicts, evidence-required criteria, no-guess unknowns, listing names grounded at threshold 1.0, CV/SOP gated by `verify_claim_grounded`, provenance on every record, deterministic conflict resolution, and a failure path that never reports "none found".

**Weak.**
- `VERIFIED` means "re-sighted on an approved listing" (M-20). With 34/34 `VERIFIED` and 34/34 missing deadlines, the label currently overstates what is known. The Blueprint's wording ("verified against the official source") needs either a real official-page confirmation step or a rename to something like `listed`.
- Only the scholarship name is grounded from listings; provider, country, field, funding and degree are accepted as extracted.
- Lexical grounding is brittle against paraphrase, ignores non-Latin text, and the bare `ground_check` passes numeric overstatement (ADR-0003, accepted).
- The failure direction is safe (false rejection, not fabrication), which is the right bias.

### 4.e Freshness and monitoring

**State.** The machinery is real: hourly scheduler, advisory locks, retry/backoff, health derivation, nine lifecycle states, stale sweep, alerts, retention. The gaps are efficiency and reach, not correctness:
- Every run re-fetches and re-extracts every monitored source with the LLM (`bypass_cache=True`), with no content hash, ETag or conditional GET (M-36).
- `update_frequency` is stored but does not influence the schedule: an annual source is checked 24 times a day.
- Only deadline, funding and degree are diffed; requirement changes are invisible (ADR-0006).
- Only 2 of 5 sources are monitored; the other 3 (`discovery_role=false`) have never been fetched (`last_checked_at` NULL).
- A silent 429 degrades monitoring into logged failures; there is no budget awareness, so the quota is spent by whichever caller comes first.

---

## 5. Gemini quota model (free tier: 20 requests/day for gemini-2.5-flash, observed)

All figures are estimates **(E)** unless stated. Call counts come from the call-site table in the audit (`core/llm.py` has no retry, quota guard or response cache).

### 5.1 Current load

| Source of calls | Basis | Calls/day |
|---|---|---|
| Monitor: listing extraction | 2 sources (measured) x 24 runs/day (configured, 60 min); every run extracts (`bypass_cache=True`) | **~48 (E)** |
| Classify fallback on new/changed candidates | ≤1 per ingested candidate; rules resolve most | ~0-3 (E) |
| Candidate validation | 1 extraction per candidate, 7-day recheck, 2 pending now | ~0.3 (E) |
| Alert soft-preference re-match | 0 users today | 0 |
| **Total from background work** | | **~48-51 (E), 2.4x the cap** |

Evidence it already bites: a real `429 RESOURCE_EXHAUSTED` is recorded in `source_fetch_log` for HEC on 2026-10-24 06:54 UTC.

### 5.2 Ways to stay within the free tier

| # | Technique | Effect on monitoring | Notes |
|---|---|---|---|
| S1 | **Content-hash skip**: store a SHA-256 of the tag-stripped page text per source; skip extraction when unchanged. Add ETag / Last-Modified conditional GET before even downloading | 48 -> roughly the number of real page changes, ~0-2/day (E) | Listing pages for annual and weekly programmes rarely change |
| S2 | **Per-source cadence from `update_frequency`**: annual weekly-check, weekly daily-check, monthly weekly-check | Without S1: 1/7 + 1 = ~1.1 calls/day for the two current sources (E) | The field already exists and is unused for scheduling |
| S3 | **Deterministic extraction first**: per-source CSS/regex rules in the existing `extraction_rules` JSONB; LLM only as fallback or to *propose* rules once, validated by a test fetch | 0 calls on stable sources | `detect_candidate_links` and `listing_diff` already show the deterministic pattern |
| S4 | **Central LLM budget service**: daily counter in Postgres, priorities user-facing > backfill > monitoring > validation, refuse or defer low priority when budget is low, honour the `RetryInfo` delay in 429 responses | Prevents background work starving users | `core/llm.py` is the single choke point |
| S5 | **Cache user-facing answers** keyed by (scholarship, question, content hash); cache soft-match results by (profile version, scholarship content hash) | Repeat questions cost 0 | |
| S6 | **Deterministic pre-filter before LLM**: hard constraints (0 LLM) shortlist the top K, LLM only for soft criteria on the shortlist | See G2 below | |
| S7 | **Local embeddings** (BP A2 default) instead of Gemini embeddings | Removes the embed call from Q&A and indexing | Current `embed_texts` goes through `core/llm.py`; confirm before relying on a separate quota (not verified) |
| S8 | **Recorded fixtures for CI/evals** | 0 live calls in CI | The new offline discovery test already follows this pattern |
| S9 | Paid tier or a second model for batch work | Real fix if usage grows | Free-tier limits for other models are **not verified** here; check before designing around them |

**Combined target after S1+S2+S4: monitoring ~1-2 calls/day (E), leaving ~18 for users and backfill.**

### 5.3 Calls each Missing/Partial gap would add

| Gap (matrix IDs) | Added LLM calls | Fits free tier? | Mitigation |
|---|---|---|---|
| G1 Follow `detail_url`, extract deadline and requirements; official-page confirmation (M-17, M-20, M-28, M-46) | One-off backfill ~34 pages = 34 calls (E); steady state ~0-2/day (E) | Yes if the backfill is throttled to ≤12/day over ~3 days after 003 | S1 on detail pages, S3 where markup is stable, S4 priority "backfill" |
| G2 Batch "match me" over stored scholarships (M-45, M-46) | Hard constraints: 0. Soft criteria: 1 call per (profile, scholarship) | ~5-10 calls/day per active user (E): free tier supports about 1-2 active users | S5, S6 (shortlist top 5), defer soft matching until a scholarship is opened |
| G3 Source discovery at scale (M-09, M-12, M-14) | Validation 1 call per candidate; ~20 candidates/week = ~3/day (E) | Yes at that rate | Deterministic pre-checks first (reachability, robots, sitemap/JSON-LD, TLD patterns); LLM only for survivors |
| G4 Q&A over discovered scholarships (M-51, M-52) | 1 embed + 1 generate per question; indexing 1 embed batch per scholarship | ~10 calls/day at 5 questions/day (E) | S5, S7; escalate to live fetch only when grounding is weak |
| G5 Other materials and draft loop (M-58, M-63) | 2-4 calls per document (draft + polish, with retry) | ~4-8/day at 2 documents/day (E) | Per-user daily budget, S4 |
| G6 Orchestrator with NL constraint parsing (M-48, M-49) | +1 call per free-text request if LLM-parsed | ~5/day (E) | Rules and a small deterministic filter grammar first; LLM only as fallback |
| G7 Requirement-change detection (M-35) | 1 call per changed page | ~0-2/day (E) with S1 | Hash first |
| G8 Country normalisation, ISO mapping, GPA scale, role gating, deadline reminders, Unicode grounding (M-08, M-18, M-38, M-41, M-54) | **0** (deterministic) | Yes | Use a standard country library and rules |
| G9 New evals and KPIs (M-69, M-71) | 0 in CI with recorded fixtures | Yes | S8 |

**Bottom line (E):** with S1+S2+S4 in place the system can afford a handful of users, one throttled backfill, and light Q&A on the free tier. Multi-user batch matching and heavy generation do not fit 20 calls/day; they need a paid key or aggressive caching. That is a product decision to make explicitly, not discover at 429 time.

---

## 6. Prioritised roadmap

Folder names follow the existing `specs/00N-*` convention. Each item lists scope, the matrix gaps it closes, why in this order, and free-tier feasibility.

**Build order (updated 2026-10-25 per ADR-0012): 003 -> 004 -> 006 -> 005 -> 007 -> 008/009.** Source expansion (006) is pulled ahead of matching (005) but only after quota governance (003) and trustworthy records (004). Folder numbers are stable identifiers, not build order. The free tier is kept until 006, where the paid-tier decision is taken.

### 003 `quota-governance-and-cheap-monitoring` (do first)
- **Scope:** LLM budget service with priorities and 429 back-off in `core/llm.py`; content-hash skip and ETag/Last-Modified; per-source cadence from `update_frequency`; record provider errors in `source_fetch_log` (not just logs); per-source `extraction_rules` hook (deterministic extraction first); budget visible in `/metrics`.
- **Closes:** M-36, M-23 (provider-error gap), M-35 (hash), part of M-33.
- **Why first:** every other item adds LLM load, and monitoring alone is already 2.4x over the cap. Without a guard, new features compete with background work and fail unpredictably.
- **Quota:** 48 -> ~1-2 calls/day (E). Hotfix option: set a longer monitoring interval today (config only).

### 004 `trustworthy-scholarship-records`
- **Scope:** follow `detail_url` to extract deadline and requirements; the official-page confirmation step and the `listed` / `verified` split defined by ADR-0013 (`verified` needs the scholarship's own official page with a deadline and eligibility present; the 34 current rows are reclassified to `listed` by a data migration and promoted only after confirmation); **a minimal operator restriction on source approve/reject (ADR-0012), as its own small slice**; backfill the 34 existing rows; country normalisation (ISO codes plus multi-country support) and fix `usa`/`USA`; classify improvements; fix or retire the EACEA extraction (facet-list page); populate `requirements`/`funding_details`; index documents for Q&A on discovered records; first extraction and verification evals with recorded fixtures.
- **Closes:** M-08 (minimal restriction), M-17, M-18, M-20, M-28, M-46 (data side), M-52, M-56/57 (their requirement dependency), M-69 (partly).
- **Why second:** matching, Q&A, generation and reminders all consume this data; building them first would produce confident-looking `unknown`s.
- **Quota:** backfill ~34 calls throttled to ≤12/day (E), steady state ~0-2/day (E); relies on 003.

### 005 `match-any-profile`
- **Scope:** hard-constraint evaluators for country/residence, field, funding, language, age, spouse/dependent; GPA scale normalisation; **country matching that handles names and aliases (for example "USA", "US", "United States", local-language names, and demonyms such as "Pakistani" for nationality), using a deterministic alias table with ISO codes as the canonical form, applied to both user input and stored values**; nationality-country mapping; profile fields for age, languages, finances, dependants; batch "match me" endpoint with deterministic pre-filter and shortlist soft-matching; discovery results ranked by match; populate `required_documents` and `remaining_actions`; intent-specific response schemas.
- **Closes:** M-04, M-12 (country filter), M-13, M-41, M-43, M-45, M-46, M-53.
- **Why now in the order (built after 006):** it turns 004's data into value for any user, and the hard-constraint work is LLM-free. Storage-side country normalisation (ISO codes, `usa`/`USA`, multi-country values) is done in 004; alias handling for user input and matching is done here.
- **Quota:** hard constraints 0; soft matching ~5-10/day per active user (E): fits only a few users on the free tier (S5, S6).

### 006 `source-coverage-at-scale`
- **Scope:** fuller operator roles and an approval queue (the minimal restriction ships earlier, in 004); a per-source LLM cost budget and the paid-tier decision as the spec's first question (ADR-0012); jurisdiction/level modelling (country, region/state, institution, parent source); bulk candidate import from curated official directories (for example ministry institution lists) with deterministic pre-checks and robots/ToS recording; country seed packs under governance review; per-source extraction rules; multilingual keyword lists and Unicode-aware grounding; pull the `universities`/`programs` tables into use.
- **Closes:** M-08, M-09, M-10, M-11 (partly), M-12, M-14, M-16, M-54.
- **Why third in build order:** scaling coverage before records are trustworthy and quota is governed multiplies untrusted data and LLM spend, so it follows 003 and 004. The owner pulled it ahead of matching (005); this reverses the BP §36 Phase 6 label and is recorded in ADR-0012.
- **Quota:** validation ~3/day for ~20 candidates/week (E) with deterministic-first checks.

### 007 `application-assistance-complete`
- **Scope:** mount the orchestrator behind an API with clarification (FR-ROUTE) and deterministic constraint parsing; remaining written materials (motivation letter, study plan, research proposal, recommendation guidance); draft review/approve/edit states; tracker with saved scholarships and deadlines; deadline reminders; async job pattern for long calls.
- **Closes:** M-38, M-48, M-49, M-58, M-60, M-63, M-68, M-51 (live-fetch escalation).
- **Why fifth:** needs 004's requirement data and 005's matching to be useful.
- **Quota:** ~4-8 calls/day per active user for generation (E); per-user budgets from 003.

### 008 `frontend` (React + TypeScript)
- **Scope:** the BP §5 screens over the stable API; typed client from OpenAPI; coverage dashboard (M-27).
- **Closes:** M-67, M-27.
- **Why here:** it has been deferred by decision, and building it earlier would chase a moving API. It is also the largest practical gap for a real user, so it can start in parallel once 005 stabilises the API surface.
- **Quota:** none.

### 009 `quality-evals-and-kpis` (can run alongside 004-006)
- **Scope:** extraction, verification and discovery evals; checker-accuracy set (BP §25.1b); ratify thresholds (M-77); KPI endpoint for PRD KPI-3..9; recorded-fixture LLM tests.
- **Closes:** M-69, M-71, M-77.
- **Quota:** 0 live calls in CI.

### Quick wins that need no feature folder
- Config: lengthen `MONITORING_INTERVAL_MINUTES` until 003 ships.
- Operator restriction on source approve/reject (M-08): by decision it ships as a small slice of 004 (ADR-0012); it can be pulled earlier as a hotfix if desired.
- Decide whether the 3 unmonitored seeds (DAAD, Stipendium Hungaricum, KAUST) should be enabled, retired, or kept as verification-only; today they are neither fetched nor monitored.

---

## 7. Owner decisions (recorded 2026-10-25)

| # | Question | Decision | Recorded in |
|---|---|---|---|
| 1 | Quota stance | **Stay on the free tier for now.** Decide on a paid key at feature 006. 003 is therefore mandatory, and multi-user batch matching stays out of reach until then. | ADR-0012 |
| 2 | Pull forward Phase 6 source expansion? | **Yes, in priority, but built after 003 and 004.** Build order is 003, 004, 006, 005, 007, 008/009. Supersedes the BP §36 Phase 6 label for source expansion (M-10). | ADR-0012 |
| 3 | What should "verified" mean? | **Confirmed on the scholarship's own official page, with a deadline and eligibility present.** A record seen only on a listing gets a weaker `listed` status. The 34 current rows are reclassified to `listed`. | ADR-0013 |
| 4 | Source approval is unrestricted (any logged-in user) | **Restrict it.** Minimal operator restriction in feature 004; fuller roles and a reviewer queue in 006. | ADR-0012; §6 features 004 and 006 |
| 5 | Country matching | **Must handle names and aliases**, folded into feature 005 (storage-side normalisation is in 004). | §6 features 004 and 005 |

**Still open:** what to do with the 3 unmonitored seed sources (DAAD, Stipendium Hungaricum, KAUST): enable, retire, or keep as verification-only. It is a question for the 004 or 006 specs, not blocking 003.

## 8. What I could not verify

- Exact behaviour of Q&A when grounding is weak beyond the audit's description (M-51).
- Absence of refresh-token and password-reset routes beyond the audit's route list (M-02).
- Whether `/coverage` exposes universities and providers covered (M-24 is marked Built on the strength of the 002 spec and tests, not a response inspection).
- Free-tier limits for models other than gemini-2.5-flash, and whether local embeddings are already supported in `tools/embed.py` (S7, S9).
- Real-user behaviour: there is no production usage (0 users), so every match, Q&A and generation quality statement here is from code and tests, not from observation.
