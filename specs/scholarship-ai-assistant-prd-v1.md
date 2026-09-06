# Scholarship AI Assistant — Product Requirements Document (PRD) v1

| Field | Value |
|---|---|
| PRD version | v1.0 (FINAL) |
| Status | **FINAL / LOCKED** — all 8 Open Decisions approved & incorporated (Stage 1 complete) |
| Last updated | 2026-09-05 |
| Source of truth | Master Blueprint v1.5 (architecture) — this PRD does not redesign it |
| Document type | Product requirements (WHAT / WHY). Architecture (HOW) stays in the Blueprint. |

**Truth hierarchy.** Blueprint > PRD for structural/architecture decisions; PRD defines product behavior and scope. Section references like *(BP §n)* point to the Master Blueprint for traceability. Requirement IDs (`FR-*`, `NFR-*`) are for downstream Spec-Driven Development.

---

## 1. Document Overview

- **Purpose:** Formalize the product-level requirements for Scholarship AI Assistant so they can be traced into the Blueprint → Phases → Workstreams → Specs → Tests.
- **What this is:** The authoritative statement of *what* the product must do and *why*.
- **What this is not:** An architecture document, an implementation spec, or a rewrite of the Blueprint. Architectural mechanisms (agents, workflows, Qdrant, MCP, LangGraph) appear only as **constraints/dependencies**, not as PRD requirements.
- **Audience:** Product, engineering (Blueprint/Spec authors), and QA.

## 2. Product Vision

Scholarship AI Assistant is an **agentic scholarship intelligence and application-assistance platform** — not a chatbot. It helps a student **discover** scholarships worldwide, **understand** their requirements, **match** them to a personal profile with an explainable verdict, **answer** grounded scholarship questions, **plan and prepare** application materials, **track readiness**, and eventually **assist with submission** under human approval *(BP §1, §64-equivalent vision)*.

It supports Bachelor's, Master's, and PhD scholarships, and — within a governed source strategy — international, country-specific, university, government, provider, and research/fellowship opportunities *(BP §1, §2)*.

## 3. Problem Statement

Students face fragmented, fast-changing scholarship information: opportunities are scattered across less-indexed official and country-specific sources; eligibility and benefits (funding, spouse/dependent, visa, test requirements) are hard to interpret; and generic web/LLM search misses or fabricates details *(BP §Discovery problem, §29–§32)*. Students need a system that finds broadly, verifies against official sources, matches transparently, and helps prepare applications without inventing facts.

## 4. Product Goals

- **G1** Maximize *practical, measurable* global scholarship coverage via a governed multi-source strategy — never claim 100% coverage *(BP §30)*.
- **G2** Give explainable, evidence-backed matching against a constraint-typed profile *(BP §18)*.
- **G3** Answer scholarship questions grounded in verified official sources; never fabricate *(BP §32, §34)*.
- **G4** Help prepare complete, requirement-aware application materials without inventing user facts *(BP §16, §17)*.
- **G5** Keep submission human-approved and never autonomous *(BP §17, §37)*.
- **G6** Demonstrate genuine Agentic AI where reasoning is required, without over-engineering *(BP §7–§9)*.

## 5. Target Users / Personas

- **Primary (MVP): The individual scholarship applicant** — a BS/MS/PhD-seeking student (the product owner is the first user; MVP is a single-user personal tool) *(BP §2)*. Needs global discovery, eligibility clarity, and application preparation help.
- **Secondary (Future SaaS): Multiple independent applicants** — same needs at scale, with tenant isolation *(BP §26, §59-equivalent)*.
- **Decision (OD-7, approved):** The **primary MVP persona is the individual scholarship applicant / student**. Secondary personas (education consultants, agencies, institutions, advisors) are **explicitly Future** and shall **not** be silently added to MVP scope. MVP requirements shall not expand around them.

## 6. User Problems & Jobs-to-be-Done

- "Find scholarships worldwide that fit me" — including less-obvious countries *(BP §30.4)*.
- "Tell me if I'm eligible and *why*" — explainable, not a black-box score *(BP §18)*.
- "Answer a specific question (spouse allowed? GRE required? stipend?) with proof" *(BP §34)*.
- "Check this scholarship URL against my profile" *(BP §3.2, §5.1)*.
- "Plan my application and tell me what documents I still need" *(BP §17)*.
- "Prepare my CV/SOP to the scholarship's actual requirements, using only my real information" *(BP §16)*.
- "Track what's ready and what's missing" *(BP §17)*.

## 7. Product Scope

In scope (product capabilities): authentication; living profile; governed multi-source discovery; scholarship intelligence records; explainable matching; scholarship-URL matching; grounded Q&A; dedicated capability entry points; application planning; document intelligence; CV/SOP generation; application assistant; application tracking/readiness; source verification & coverage; structured outputs. Future: continuous monitoring, coverage analytics, assisted live submission, SaaS multi-tenancy *(BP §2, §36)*.

## 8. MVP Scope

Per Blueprint capability labels *(BP §36)*, the MVP includes:
- Auth + user profile (constraint-typed) — `FR-AUTH`, `FR-PROFILE`
- Controlled Source Registry + initial high-quality source set — `FR-DISC`
- Multi-source discovery + normalization + deduplication + official-source verification — `FR-DISC`, `FR-VERIFY`
- Scholarship database + semantic retrieval where justified — `FR-INTEL`
- Profile matching (hard/soft/exclusion) — `FR-MATCH`
- Scholarship URL matching — `FR-URL`
- Scholarship Q&A + structured responses — `FR-QA`, `FR-OUT`
- Basic application planning + document requirement tracking — `FR-PLAN`, `FR-DOC`
- Basic CV/SOP assistance — `FR-GEN`
- Basic coverage summary (counts + gaps) — `FR-COV`

## 9. Future Scope

- Application/Submission Assistant orchestration (prepare + approvals, no live send) — Post-MVP *(BP §36, Phase 4)*.
- Scheduled source monitoring + notifications, coverage analytics, source-health, semi-automated candidate-source validation — Production *(Phase 5)*.
- Large-scale source/country expansion, **live approval-gated submission**, SaaS multi-tenancy/billing/quotas — Future/Advanced *(Phase 6)*.

## 10. Core User Journeys

- **J1 Discovery → Match → Q&A → Plan:** profile → "find scholarships" → ranked verified results → open one → ask questions → create plan *(BP §3.1, §5.1)*.
- **J2 URL match:** paste a scholarship URL → verdict + gaps + evidence → next action (Q&A / plan / documents) *(BP §3.2)*.
- **J3 Application preparation:** select scholarship → plan → identify materials → upload docs → CV/SOP generated → readiness → human review → (future) submit *(BP §3.3, §17.1)*.

## 11. Functional Requirements (index)

Grouped below in §12–§22. Each capability has **Input → Processing → Output → Next Action** where user-facing *(BP §5.1)*. Processing names product behavior, not architecture.

### 11.1 Intent Routing & Clarification Behavior  (`FR-ROUTE`)  *(OD-1, approved)*

- **FR-ROUTE-1** When the system cannot confidently determine the user's intent or the correct capability, it shall **not guess** and shall **not take an incorrect action** on an uncertain interpretation.
- **FR-ROUTE-2** In such cases the system shall **request clarification** from the user before proceeding.
- **FR-ROUTE-3** When a request legitimately spans multiple capabilities, the system may determine and execute an **appropriate sequence** of actions. *(Product behavior only; orchestration mechanism is a Blueprint concern.)*

## 12. Scholarship Intelligence Requirements  (`FR-INTEL`)

- **FR-INTEL-1** The product shall represent, **where applicable**, per scholarship: name, provider, country, university, program, degree level (BS/MS/PhD/other), field, funding type (see FR-INTEL-5 classification), tuition coverage (amount/percentage where available), stipend, accommodation, travel/airfare, health insurance, visa information, spouse/dependent policy & family benefits, eligibility, nationality restrictions, academic/GPA requirements, IELTS/TOEFL/PTE, GRE/GMAT, age restrictions, work experience, application fee, deadline, intake, required documents, application procedure/method, official application URL, official scholarship URL, official source, opening date, current/closing status, conditions, exceptions, notes *(BP §14)*.
- **FR-INTEL-2** No scholarship is assumed to contain every field. Each field carries a **value-status**: `known / unknown / not_applicable / conditional / conflicting`, plus a confidence and provenance *(BP §14)*.
- **FR-INTEL-3** Missing information is stored/shown as `unknown` and **never guessed** *(BP §14, §32)*.
- **FR-INTEL-4** Each record carries source provenance and a **last-verified date** *(BP §32)*.
- **FR-INTEL-5 (OD-6, approved)** Funding shall **not** be represented as a simplistic "funded / not funded" flag. It shall use a benefit-status classification: **Fully funded · Substantially funded · Partially funded · Tuition-only · Stipend-only · Other benefit combination · Unknown / not verified**, and shall represent conditions/uncertainty where applicable. The product shall **not** impose a universal percentage threshold for "fully" or "substantially" funded *(BP §14 funding_details)*.

## 13. User Profile Requirements  (`FR-PROFILE`)

- **FR-PROFILE-1** The student shall create and maintain a **living, persistent, updateable** profile (not a one-time form) *(BP §14)*.
- **FR-PROFILE-2** The profile shall support: personal/academic info, education, current degree, target degree/level, target fields, countries/regions, nationality, academic performance/GPA, language tests (IELTS/TOEFL/PTE status), GRE/GMAT status, work/research experience, publications/projects, funding preferences, accommodation/travel preferences, visa/spouse/dependent considerations, skills/background *(BP §14, vision)*.
- **FR-PROFILE-3** The profile shall explicitly classify each criterion as **Hard constraint / Soft preference / Exclusion**, and track **Missing/unknown information** separately *(BP §14 `profile_criteria`, `missing_info`)*.
- **FR-PROFILE-4** Matching (`FR-MATCH`) shall consume these categories differently (see §15).

## 14. Discovery Requirements  (`FR-DISC`)

**Input:** profile + constraints. **Processing:** governed multi-source retrieval → data-quality pipeline. **Output:** ranked, verified, source-attributed results + coverage summary. **Next action:** open / match / Q&A *(BP §3.1, §5.1)*.

- **FR-DISC-1** Discovery shall prioritize fresh, official, and broad-geography sources: government, university (incl. department where relevant), providers/foundations, country-specific portals, international organizations, research/fellowship sources, approved APIs/connectors *(BP §29, §30.1)*.
- **FR-DISC-2** The product shall **not** rely on unrestricted random web search as its primary strategy *(BP §2, §29)*.
- **FR-DISC-3** A **human-defined, controlled Source Registry** shall define trusted sources and their rules; runtime AI/tools operate only within those boundaries *(BP §29)*.
- **FR-DISC-4** Discovery shall support: source discovery, extraction, normalization, deduplication, official-source verification, freshness tracking, expired/closed handling, newly discovered opportunities, and country/source coverage tracking *(BP §31, §32, §30.4)*.
- **FR-DISC-5** Newly discovered sources become **candidates** requiring governed approval; they are never treated as authoritative automatically *(BP §29)*.
- **FR-DISC-6** Discovery shall support systematic query dimensions (country, university, degree level, field, nationality, funding type, intake, requirements, deadlines) rather than a single generic query *(BP §30.3)*.
- **FR-DISC-7** The product shall **not** claim 100% global coverage; it shall target maximum practical coverage with measurable metrics (§21) *(BP §30.4)*.

## 15. Matching Requirements  (`FR-MATCH`)

**Input:** profile + scholarship requirements. **Processing:** deterministic evaluation of hard constraints + reasoned evaluation of soft/fuzzy criteria. **Output:** explainable verdict. **Next action:** Q&A / plan *(BP §18, §7.5)*.

- **FR-MATCH-1** Matching shall evaluate **hard constraints, soft preferences, exclusions, and unknown/missing information**, plus structured eligibility and, where needed, fuzzy criteria *(BP §18)*.
- **FR-MATCH-2** Results shall be **explainable**, not an opaque percentage. The user shall see: what matches, what does not, what is unknown, *why*, and the supporting evidence *(BP §18)*.
- **FR-MATCH-3** A **hard-constraint failure** shall be treated differently from a soft-preference mismatch (hard fail → not a match; soft miss → lowers strength/rank) *(BP §18)*.
- **FR-MATCH-4** Output shall use a fixed, structured verdict object: eligibility verdict (Eligible / Likely / Possibly / Not / Unknown-requires-verification) + match-strength (Strong / Possible / Not) + hard/soft/exclusion breakdown + missing information + evidence *(BP §18)*.
- **FR-MATCH-6 (OD-3, approved)** Matching shall be **intelligent** (retain reasoning over soft/fuzzy criteria — do not reduce to rules-only) and shall report **criterion-level outcomes**: **Pass / Fail / Unknown–Needs Verification / Inferred** (Inferred used where a value was reasoned rather than verified). Categories: **hard constraints** (nationality, degree level, GPA, mandatory requirements, deadlines, explicit eligibility), **exclusions** (attributes that disqualify), **soft/fuzzy criteria** (work-experience relevance, academic-field alignment, nuanced preference fit). **Product rule:** a hard eligibility failure must never be overridden merely because other factors are strong *(BP §18, §7.5)*.
- **FR-MATCH-5** *(Product-behavior constraint)* Eligibility "facts" (hard constraints) shall be decided deterministically and shall not be overridable by model judgment *(BP §7.5 guardrails; architecture in Blueprint)*.

## 16. Scholarship-URL Matching Requirements  (`FR-URL`)

- **Input:** scholarship URL + user profile.
- **Processing:** fetch official page → extract requirements → verify → compare against profile *(BP §3.2, §5.1)*.
- **Output:** match verdict · matched criteria · failed criteria · missing information · evidence · official source.
- **Next action:** ask a question · create application plan · start document preparation.
- **FR-URL-1** The user shall be able to match a **specific known scholarship by URL** without going through full discovery *(BP §5.1)*.

## 17. Q&A Requirements  (`FR-QA`)

- **Input:** question + scholarship/source context. **Output:** structured, evidence-based answer + official source + last-verified + confidence. **Next action:** follow-up / match / plan *(BP §5.1, §34)*.
- **FR-QA-1** The user shall be able to ask about eligibility, funding, stipend, accommodation, visa, spouse/dependents, required tests, GPA, documents, deadline, application process, conditions/exceptions *(BP §34)*.
- **FR-QA-2** Answers shall be **grounded in verified scholarship information**, structured, and controlled — not unrestricted generic LLM output *(BP §32, §34)*.
- **FR-QA-3** When information cannot be verified, the answer shall state **could-not-confirm / Unknown** rather than guess *(BP §32)*.

## 18. Document & Application Requirements  (`FR-DOC`, `FR-GEN`)

- **FR-DOC-1** The system shall request missing documents, accept uploads, parse/extract information, associate them to the profile/application, compare against the specific scholarship's requirements, detect missing info and inconsistencies where possible, track readiness, and tell the user exactly what remains *(BP §16)*.
- **FR-GEN-1** CV/SOP and other written materials shall be **requirement-aware** — following a university's specified CV format (e.g., Europass) or specified SOP/motivation questions where applicable *(BP §16)*.
- **FR-GEN-2** Generated materials shall be grounded in the user profile, user-provided documents, and verified scholarship/university/program requirements; the system shall **never fabricate** achievements, experience, publications, employment, grades, or certificates *(BP §16)*.
- **FR-DOC-2** Supported materials (only where the specific scholarship requires them): transcript, degree certificate, passport/ID info, CV, Europass CV, SOP, motivation letter, personal statement, study plan, research proposal/plan, recommendation-letter guidance + recommender info, certificates, language-test docs, GRE/GMAT docs, portfolio, publications, work-experience docs, character certificate, financial docs, scholarship/university-specific forms, other written materials *(BP §17.2)*.
- **FR-DOC-3** Requirements shall be **derived dynamically** from the specific scholarship/university/program — not assumed uniform *(BP §17.2)*.

## 19. Application Planning Requirements  (`FR-PLAN`)

- **FR-PLAN-1** A user shall be able to generate a **structured application plan** for a selected scholarship **without** using the other capabilities *(BP §17, §5.1)*.
- **FR-PLAN-2** The plan shall turn verified requirements into a labeled checklist: `Complete / Missing / User-must-obtain / AI-can-generate / Needs-human-review / Needs-official-verification` *(BP §17)*.

**Application Assistant (`FR-APP`) — Post-MVP** *(BP §36, Phase 4)*:
- **FR-APP-1** Support the journey: scholarship selected → understand requirements → plan → identify materials → request missing docs → identify gaps → prepare/review CV, SOP, other materials → readiness check → user approval → (future) submission *(BP §17.1)*.
- **FR-APP-2** The assistant shall be available as a **step-by-step guided mode** and as **individual deterministic buttons** producing the same result *(BP §17, §5.1)*.
- **FR-APP-3 (OD-2, approved) — Future live submission behavior:** for the future assisted-submission capability, the product shall: retry only **safe/idempotent** operations where appropriate; **never blindly repeat** dangerous or irreversible actions; **clearly communicate submission status** to the user; **record/make failures visible**; require **explicit human approval** for final submission; and provide a **final user review opportunity immediately before** any irreversible submission action *(BP §37)*. *(Future capability — not MVP.)*

## 20. Application Tracking / Readiness  (`FR-TRACK`)

- **FR-TRACK-1** The product shall track saved scholarships, applications, deadlines, missing documents, tasks, and status; and provide a **readiness assessment** using the labels in FR-PLAN-2 *(BP §17)*.
- **FR-TRACK-2** Interview stage and results tracking are **future** *(BP §17)*.

## 21. Authentication & Account Requirements  (`FR-AUTH`)

- **FR-AUTH-1** The product shall provide **Sign up, Login, Logout**, and session/account management *(BP §19, §20)*.
- **FR-AUTH-2** User data shall be **isolated per user** from the MVP, enabling later multi-user SaaS without redesign *(BP §19)*.

## 22. Source / Verification Requirements  (`FR-VERIFY`, `FR-COV`)

- **FR-VERIFY-1** The philosophy shall be: **human-defined trusted-source boundaries → controlled runtime access → extraction → normalization → deduplication → verification → structured intelligence → matching/Q&A/application assistance** *(BP §29, §31)*. **(OD-5, approved)** The governed source ecosystem includes, where applicable: government scholarship portals, official university websites, university department/program pages, official country education portals, providers/foundations, international organizations, research/fellowship sources, and approved scholarship APIs/connectors. The **initial seed source list is not enumerated in this PRD** (no invented list of sites) and is set operationally under governance.
- **FR-VERIFY-2** The LLM shall **not** freely decide which websites are authoritative; source authority is governed *(BP §29)*.
- **FR-VERIFY-3** Third-party APIs/aggregators may be **discovery signals**; the **official source is the preferred final authority** for factual verification *(BP §32)*.
- **FR-VERIFY-4** Each record shall carry: source, source type, official/non-official status, retrieved_at, last_verified_at, current status, deadline, verification status, evidence/provenance *(BP §32)*.
- **FR-VERIFY-5** The product shall distinguish record lifecycle states: newly discovered, verified, unverified, updated, expired, closed, reopened, stale, source-unavailable *(BP §32)*.
- **FR-VERIFY-6** A **failed source retrieval shall not** be interpreted as "no scholarship exists"; failures are logged and retried per policy *(BP §33)*.
- **FR-VERIFY-7** When sources conflict, a defined **conflict-resolution rule** shall prefer the current official source (then more-recent, then higher-reliability); unresolved conflicts are surfaced, not silently chosen *(BP §32)*.
- **FR-COV-1** The product shall expose **measurable coverage**: registered/active/checked/failed sources, last-checked, countries/universities/providers covered, new candidates, verified opportunities, and coverage gaps *(BP §30.4)*.

## 23. Structured Output Requirements  (`FR-OUT`)

- **FR-OUT-1** Responses shall be **structured and controlled**, using intent-specific schemas (eligibility, funding, spouse/dependent, application, matching) — not a single universal template and not unrestricted conversational output *(BP §34)*.
- **FR-OUT-2** Every factual response shall include, where applicable: evidence/source reference, verification status, last-verified date, and next action *(BP §34)*.
- **FR-OUT-3** The product shall avoid: unsupported claims presented as fact, opaque matching scores, and unverified information stated as fact *(BP §18, §32, §34)*.

## 24. Agentic AI Product Requirements  (`FR-AGENTIC`)

- **FR-AGENTIC-1** The product shall demonstrate genuine Agentic AI: autonomous reasoning/planning/orchestration is used **only where required**; deterministic processes use workflows; reusable logic uses services; external access uses tools/connectors *(BP §7–§9)*.
- **FR-AGENTIC-2** Sub-agents shall be introduced only when a single agent genuinely cannot handle a responsibility; unnecessary sub-agents are prohibited *(BP §8)*.
- **FR-AGENTIC-3** *(Behavioral)* Agents that underpin facts (matching, verification) shall be **guardrailed** so model judgment cannot override deterministic facts *(BP §7.5, §32)*.
- *Note:* the specific agent set and boundaries are an **architecture concern** (Blueprint §7–§9), not prescribed here.

## 25. Non-Functional Requirements  (`NFR`)

- **NFR-1 Explainability:** matching and answers must be explainable with evidence *(BP §18, §34)*.
- **NFR-2 Groundedness:** no fabricated scholarship facts or user facts *(BP §16, §32)*.
- **NFR-3 Modularity/evolvability:** MVP → Production → SaaS without rewrite *(BP §2, §26)*.
- **NFR-4 Context efficiency (dev):** the product shall be buildable in independently executable units with artifact-based handoff *(BP §39–§42)* — mechanism owned by Blueprint/process docs.
- **NFR-5 Usability:** dedicated entry points per major intent; long operations must not block the UI *(BP §5, §5.1)*.

## 26. Security & Privacy Requirements  (`NFR-SEC`)

- **NFR-SEC-1** Authentication, authorization, and **per-user data isolation** *(BP §19)*.
- **NFR-SEC-2** Secure handling of sensitive academic/personal documents; secrets/API keys via env/secret management; input validation; access control; logging *(BP §19)*.
- **NFR-SEC-3** One user's information shall never be accessible to another (isolation enforced at the data layer) *(BP §19)*.

## 27. Reliability / Freshness Requirements  (`NFR-REL`)

- **NFR-REL-1** Freshness is first-class: records carry retrieved_at / last_verified_at / status; stale records are flagged. **Freshness windows are configurable per source characteristics** rather than one universal period (OD-4) *(BP §32)*.
- **NFR-REL-2** Failure/retry handling for sources, APIs, extraction, and verification; failures surface as coverage gaps *(BP §33)*.
- **NFR-REL-3 (Future)** Continuous monitoring of approved sources for new/changed scholarships, deadline changes, open/closed status, updated requirements *(BP §35, Phase 5)*. **(OD-8, approved)** Future monitoring shall support **in-app** and **email** notifications, and shall remain **extensible** to additional channels later — without expanding MVP scope to every possible channel now.

## 28. Success Metrics / KPIs

Derived from Blueprint-defined measurables. **(OD-4, approved)** The product goal is **maximum practical, measurable global coverage + freshness + official verification** — it shall **not** claim or promise discovery of 100% of scholarships worldwide. Exact numeric KPI targets are **not invented here**; they remain **future measurable targets / operational configuration**.
- **KPI-1 Source coverage:** active/checked sources; coverage-gap count *(BP §30.4)*.
- **KPI-2 Geographic/entity coverage:** country/region coverage; university/provider coverage *(BP §30.4)*.
- **KPI-3 Extraction success rate:** % of attempted extractions that succeed.
- **KPI-4 Verification rate:** % of presented facts with `verified` status + source.
- **KPI-5 Data freshness:** % records within their freshness window — **configurable per source characteristics**, not a single universal period.
- **KPI-6 Duplicate rate:** % duplicates after deduplication.
- **KPI-7 Matching quality/accuracy:** verdict-correctness matrix pass rate; **zero** hard-constraint guardrail violations (build-breaking) *(BP §25, §7.5)*.
- **KPI-8 Groundedness:** anti-hallucination pass rate; % answers with evidence; % unconfirmable correctly labeled Unknown *(BP §25, §34)*.
- **KPI-9 System failure/retry rate:** source/API/extraction/verification failure and retry counts *(BP §33)*.

## 29. Acceptance Criteria (product-level, from phase DoDs)

- **AC-1 (MVP core):** paste a real scholarship URL → explainable verdict + gaps; ask "does it require GRE?" → grounded answer with source + Verified/Inferred/Unknown label *(BP §22 Phase 1)*.
- **AC-2 (Discovery):** profile-driven discovery across ≥2 source types → ranked, verified, source-attributed results + coverage summary; a new source lands as candidate, not auto-authoritative *(BP §22 Phase 2)*.
- **AC-3 (Docs/Gen):** upload transcript → associated + satisfies a requirement; CV/SOP generated grounded only in real data *(BP §22 Phase 3)*.
- **AC-4 (Planning/Assistant):** "create the application plan" → labeled checklist + readiness; assistant requests missing items and obtains approvals; button-vs-agent parity *(BP §22 Phase 4)*.
- **AC-5 (Integrity):** matching verdict-correctness matrix passes and hard-constraint lock is never overridden (build-breaking) *(BP §25)*.

## 30. Out of Scope (for MVP)

- Live external application submission *(future — BP §37, Phase 6)*.
- Scheduled monitoring + notifications, coverage analytics dashboards, semi-automated source validation *(Production — Phase 5)*.
- SaaS multi-tenancy, billing, quotas *(Future — Phase 6)*.
- Advisor/agency/multi-role personas *(not defined)*.

## 31. Dependencies & Constraints

- **Constraint C-1:** Discovery depends on a governed Source Registry with an initial curated source set *(BP §29)*.
- **Constraint C-2:** Verified facts depend on reachable official sources; unreachable → could-not-confirm, not fabricated *(BP §32, §33)*.
- **Constraint C-3:** Human approval is mandatory before any future live submission *(BP §17, §37)*.
- **Constraint C-4 (architecture, not a PRD requirement):** agents/workflows/services/tools split, PostgreSQL/Qdrant/file-storage boundaries, MCP/API connectors, LangGraph/LangChain/OpenAI Agents SDK — defined in the Blueprint *(BP §6–§15)*.
- **Dependency D-1 (dev process):** Workstream-based, artifact-handoff development *(BP §39–§42)*.

## 32. Resolved Decisions (formerly Open) — all approved

All 8 decisions are **approved and incorporated**. None remain open at the product level for MVP.

| ID | Decision (approved) | Incorporated in |
|---|---|---|
| OD-1 | On uncertain intent: don't guess, request clarification; may sequence multi-capability requests | §11.1 FR-ROUTE-1..3 |
| OD-2 | Future submission: idempotent-only retry, no blind repeat of irreversible actions, status visible, failures recorded, explicit human approval + final review | §19 FR-APP-3 |
| OD-3 | Intelligent matching; hard/exclusion/soft categories; criterion outcomes Pass/Fail/Unknown/Inferred; hard fail never overridden | §15 FR-MATCH-6 |
| OD-4 | Max practical measurable coverage (no 100% claim); tracked KPIs; freshness configurable per source; numeric targets deferred as operational config | §28 KPIs, §27 NFR-REL-1 |
| OD-5 | Governed source ecosystem; official verification priority; new source not auto-trusted; seed list not invented | §22 FR-VERIFY-1..7 |
| OD-6 | Funding classification (Fully/Substantially/Partially/Tuition-only/Stipend-only/Other/Unknown); no universal % threshold | §12 FR-INTEL-5 |
| OD-7 | Primary MVP persona = individual applicant; consultants/agencies/institutions = Future, not silently in MVP | §5 |
| OD-8 | Future monitoring notifications: in-app + email, extensible; no MVP over-expansion | §27 NFR-REL-3 |

**Remaining operational (non-blocking) configuration** — not product Open Decisions: exact numeric KPI targets and per-source freshness windows (set during operations, per OD-4); the concrete initial seed source list (set under governance, per OD-5). These are intentionally deferred and do **not** block PRD lock.

## 33. Traceability to Existing Blueprint Requirements

| PRD area | Blueprint source |
|---|---|
| Vision, scope, MVP/future | §1, §2, §36 |
| Auth & isolation | §19, §20 |
| Living, constraint-typed profile | §14 |
| Scholarship intelligence + value-status | §14 |
| Governed multi-source discovery | §29, §30 |
| Data-quality pipeline | §31 |
| Verification, provenance, conflict, freshness | §32 |
| Failure/retry, coverage | §33, §30.4 |
| Matching (explainable, hard/soft/exclusion) | §18, §7.5 |
| URL matching | §3.2, §5.1 |
| Q&A grounded + schemas | §7.2, §34 |
| Capability entry points (I/O/O/Next) | §5, §5.1 |
| Application planning | §17 |
| Document intelligence, CV/SOP | §16, §17 |
| Application assistant | §7.4, §17.1–§17.2 |
| Tracking/readiness | §17 |
| Future submission | §37, §7.4 |
| Continuous monitoring | §35 |
| Agentic principle | §7–§9 |
| Structured output | §34 |
| Security/privacy | §19 |
| Phases / acceptance | §22, §25 |
| Dev continuity (dependency) | §39–§42 |

---

# PRD Validation Report (Stage 1 — Final)

**1. Requirements captured**
All product-level capabilities from the Blueprint plus the 8 approved decisions: auth; living constraint-typed profile; intent routing/clarification; governed multi-source discovery; comprehensive scholarship intelligence with value-status; **funding classification (OD-6)**; explainable **intelligent matching with criterion-level outcomes (OD-3)**; URL matching; grounded structured Q&A; dedicated capability entry points; application planning; document intelligence; requirement-aware CV/SOP generation; application assistant with **future-submission safety rules (OD-2)**; tracking/readiness; source verification/provenance/conflict/freshness/failure-retry; **measurable coverage & configurable freshness (OD-4)**; **governed source strategy (OD-5)**; structured output; agentic principle; security/isolation; **monitoring notifications (OD-8)**; MVP-vs-future labeling with **primary persona locked (OD-7)**; acceptance criteria.

**2. Requirements that were ambiguous — now resolved**
The three previously-ambiguous items (KPI targets, personas, funding classification) are resolved by OD-4, OD-7, and OD-6 respectively. Numeric KPI targets and per-source freshness windows are intentionally deferred as **operational configuration** (per OD-4), and the concrete seed source list as **governed operational setup** (per OD-5) — neither blocks lock.

**3. Open decisions**
**None remaining.** All 8 (OD-1…OD-8) are approved and incorporated (see §32 mapping). Deferred numeric targets / seed list are operational configuration, not product Open Decisions.

**4. Conflicts discovered / consistency audit**
No contradictions. Audit checks performed and passed:
- Matching (§15) ↔ OD-3: consistent — intelligent reasoning retained; hard failure never overridden; criterion outcomes Pass/Fail/Unknown/Inferred added.
- Coverage (§14, §22, §28) ↔ "maximum practical coverage": consistent — no 100% claim anywhere.
- Funding terms (§12) ↔ OD-6: consistent — classification set replaces "funded/not funded"; no universal % threshold.
- Persona (§5, §30) ↔ OD-7: consistent — individual applicant primary; others explicitly Future.
- Notifications (§27) ↔ OD-8: consistent — in-app + email, extensible, MVP not over-expanded.
- Future submission (§19, §30) ↔ OD-2 & human approval: consistent — explicit approval + final review + idempotent-only retry; remains Future, not MVP.
- Source verification (§22) ↔ OD-5: consistent — governed, official-priority, no auto-trust of new sources.
- Routing/clarification (§11.1) ↔ OD-1: consistent — no guessing; clarify; may sequence multi-capability requests.
- MVP-vs-Future scope: intact — no future capability silently promoted to MVP.

**5. Readiness**
The PRD is internally consistent, standalone, and requires no chat history to understand. All approved decisions are incorporated with preserved requirement IDs and traceability.

**PRD STATUS: FINAL / LOCKED ✅** (Stage 1 complete — consistency audit passed.)

*Scope note:* Excludes architecture, implementation specs, and code, per request. Ready to derive/confirm the Master Blueprint and subsequent Spec-Driven artifacts in a later stage.
