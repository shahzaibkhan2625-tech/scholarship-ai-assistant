# Feature Specification: Scholarship AI Assistant — MVP Core Platform

**Feature Branch**: `001-scholarship-mvp`
**Created**: 2026-09-06
**Status**: Draft
**Input**: User description: "Build Scholarship AI Assistant, an agentic scholarship intelligence and application-assistance platform. Read specs/scholarship-ai-assistant-prd-v1.md and the constitution first, and treat that PRD as the authoritative source of scope. Capture the whole product's what and why as user stories, functional requirements, and acceptance criteria: constraint-typed user profiles with hard, soft, and exclusion criteria and explicit missing-information handling; scholarship discovery from governed, registry-approved sources with measured coverage reporting; requirement extraction and normalization from a single official URL and from multiple sources; an explainable matching verdict where hard eligibility constraints are evaluated deterministically and are never overridden by an LLM while only soft or fuzzy criteria use LLM judgment, with each criterion marked pass, fail, unknown, or inferred and eligibility gaps listed; grounded question answering that draws only from sourced content and labels every answer verified, inferred, or unknown and never fabricates; document upload, parsing, and association, plus requirement-aware CV and SOP generation grounded strictly in the user's real data; application planning with a labeled readiness checklist; and assisted application and submission that never transmits anything to an external system without explicit per-submission human approval. Preserve the PRD's acceptance criteria AC-1 through AC-5 as testable acceptance criteria. Focus only on what and why — user stories, functional requirements, acceptance criteria — and do not specify the tech stack or implementation approach; that belongs in the plan."

**Source of truth**: `specs/scholarship-ai-assistant-prd-v1.md` (product scope) and `.specify/memory/constitution.md` (non-negotiable behavioral guarantees). This spec translates PRD §11–§30 into testable user stories, functional requirements, and acceptance criteria for the MVP. It intentionally excludes architecture/tech-stack decisions, which belong to `plan.md`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build a constraint-typed profile (Priority: P1)

A student creates an account and builds a living academic/personal profile, explicitly marking which facts are hard requirements (e.g., "must be a citizen of X"), which are soft preferences (e.g., "prefer full funding"), and which are disqualifying exclusions (e.g., "will not consider on-campus-only offers"). Facts the student doesn't yet know or hasn't provided are tracked as missing, not guessed.

**Why this priority**: Every other capability (matching, discovery ranking, Q&A relevance, document/CV generation) depends on having a structured, typed profile to reason against. Without it, nothing downstream can be explainable or grounded.

**Independent Test**: Can be fully tested by creating a profile, entering academic/personal data, classifying several criteria as hard/soft/exclusion, leaving some fields unanswered, and verifying the profile persists, is editable, and correctly reports which fields are missing — independent of any matching or discovery feature.

**Acceptance Scenarios**:

1. **Given** a new authenticated user with no profile, **When** they enter academic info, target degree/field, countries of interest, and mark nationality as a hard constraint, **Then** the profile is saved and nationality is retrievable as a hard constraint.
2. **Given** an existing profile, **When** the user updates a field (e.g., adds a new IELTS score), **Then** the profile reflects the update without requiring a full profile re-entry.
3. **Given** a profile with an unanswered field (e.g., GRE status not provided), **When** the profile is viewed or used elsewhere in the product, **Then** that field is shown as missing/unknown rather than silently omitted or assumed.
4. **Given** an existing profile, **When** the user reclassifies a criterion from soft preference to hard constraint, **Then** subsequent matching treats it accordingly.

---

### User Story 2 - Match a known scholarship by URL (Priority: P1)

A student has found a specific scholarship online and pastes its official URL. The system extracts the scholarship's requirements from that page, compares them against the student's profile, and returns an explainable verdict: what matches, what fails, what is unknown, and why — never a bare score.

**Why this priority**: This is the core, fastest-to-value product moment (PRD AC-1): a student with one link in hand gets a trustworthy, evidence-backed answer instead of having to read and interpret a scholarship page themselves.

**Independent Test**: Can be fully tested by submitting a real, official scholarship URL with a profile that has at least one hard constraint and one soft preference, and verifying the returned verdict correctly separates pass/fail/unknown criteria with evidence — independent of the discovery pipeline.

**Acceptance Scenarios**:

1. **Given** a completed profile and a valid official scholarship URL, **When** the user submits the URL for matching, **Then** the system returns an eligibility verdict (Eligible / Likely / Possibly / Not / Unknown-requires-verification), a match-strength, and a per-criterion breakdown (Pass / Fail / Unknown–Needs Verification / Inferred), each backed by evidence from the source page.
2. **Given** a profile that fails a hard constraint on the scholarship (e.g., wrong nationality), **When** matching runs, **Then** the verdict is "Not" regardless of how strong the student's soft-preference alignment is, and the failing hard constraint is clearly identified.
3. **Given** a scholarship page missing a specific data point (e.g., no stated GRE requirement), **When** matching runs, **Then** that criterion is marked Unknown rather than assumed pass or fail.
4. **Given** a URL that cannot be fetched (page down, not found), **When** the user submits it, **Then** the system reports the fetch failure explicitly and does not report "not eligible" or fabricate a verdict.

---

### User Story 3 - Ask grounded questions about a scholarship (Priority: P1)

After viewing a scholarship (via URL match or discovery), a student asks natural-language questions such as "Does this require GRE?", "Is my spouse covered?", or "What's the stipend?" and receives a direct answer sourced from the scholarship's official content, labeled as verified, inferred, or unknown.

**Why this priority**: Completes the PRD AC-1 core journey; students need to interrogate details beyond the top-level verdict without reading dense official pages themselves, and must be able to trust that answers aren't invented.

**Independent Test**: Can be fully tested by asking a set of factual questions against a scholarship with known official content and confirming each answer is labeled with a confidence status and cites its source, including at least one question whose answer is genuinely not stated on the source (must return Unknown).

**Acceptance Scenarios**:

1. **Given** a scholarship with a stated stipend amount, **When** the user asks "what is the stipend?", **Then** the system answers with the amount, labels it Verified, and cites the official source and last-verified date.
2. **Given** a scholarship whose page does not mention spousal/dependent policy, **When** the user asks about it, **Then** the system answers "Unknown / could not confirm" rather than guessing.
3. **Given** an answer that required combining two related but not identical statements on the source page, **When** the answer is produced, **Then** it is labeled Inferred rather than Verified.
4. **Given** a question unrelated to any scholarship or sourced content (e.g., general career advice), **When** asked, **Then** the system does not answer as if it were a grounded scholarship fact.

---

### User Story 4 - Discover scholarships from governed sources (Priority: P2)

A student without a specific scholarship in mind asks the system to find scholarships matching their profile. The system searches only within an approved, human-governed set of sources (government portals, universities, providers, etc.), returning ranked, verified, source-attributed results plus a coverage summary describing what was actually searched.

**Why this priority**: Delivers the "find scholarships worldwide that fit me" job-to-be-done (PRD AC-2) but depends on profile (US1) and benefits from matching/Q&A (US2/US3) already existing to act on results, so it follows them.

**Independent Test**: Can be fully tested by running discovery against a profile with clear target criteria (degree level, field, country) and verifying results are ranked, each carries source attribution and verification status, span at least two distinct source types, and a coverage summary is returned alongside the results.

**Acceptance Scenarios**:

1. **Given** a profile with defined target degree, field, and country preferences, **When** the user requests discovery, **Then** the system returns ranked results drawn from at least two distinct governed source types, each with source, source type, and verification status.
2. **Given** a discovery run, **When** results are returned, **Then** a coverage summary accompanies them showing sources checked, sources failed, and known gaps — never a claim of complete/100% coverage.
3. **Given** discovery encounters a website not yet in the governed Source Registry, **When** it is found, **Then** it is recorded as a pending candidate source and is not used as an authoritative fact source until approved.
4. **Given** a source fetch fails during discovery, **When** results are compiled, **Then** the failure is logged and reported as a coverage gap, not interpreted as "no scholarships exist" for that source.
5. **Given** two governed sources report conflicting details for the same scholarship, **When** the record is presented, **Then** the conflict is surfaced explicitly (not silently resolved) unless the deterministic conflict-resolution rule (official > recent > reliable) clearly resolves it.

---

### User Story 5 - Prepare documents, CV, and SOP (Priority: P3)

A student selects a scholarship, uploads supporting documents (transcript, certificates, etc.), and asks the system to prepare a CV and/or statement of purpose tailored to that scholarship's specific requirements — built strictly from the student's real profile and uploaded documents.

**Why this priority**: Delivers PRD AC-3; requires a matched/selected scholarship (US2/US4) and profile (US1) to know what requirements to write against.

**Independent Test**: Can be fully tested by uploading a transcript against a scholarship that requires one, confirming it is parsed, associated with the profile, and marked as satisfying that specific requirement; then requesting a CV/SOP and confirming every stated fact traces back to the profile or an uploaded document, with no invented achievements.

**Acceptance Scenarios**:

1. **Given** a scholarship requiring a transcript, **When** the user uploads their transcript file, **Then** it is parsed, associated with their profile/application, and the transcript requirement is marked satisfied.
2. **Given** an uploaded document with extractable inconsistencies (e.g., GPA on file differs from profile), **When** processing completes, **Then** the inconsistency is flagged to the user rather than silently resolved.
3. **Given** a scholarship with a specified CV format (e.g., Europass) or specific SOP questions, **When** the user requests generation, **Then** the produced document follows that format/those questions.
4. **Given** a generated CV or SOP, **When** reviewed, **Then** every factual claim (experience, publication, grade, certificate) is traceable to the user's profile or an uploaded document — none are fabricated.
5. **Given** the user's profile lacks information needed to complete a section (e.g., no listed work experience but the format expects one), **When** generation runs, **Then** the gap is reported to the user rather than filled with invented content.

---

### User Story 6 - Plan the application and get assisted, human-approved submission (Priority: P4)

A student asks the system to build an application plan for a selected scholarship. The system turns the scholarship's verified requirements into a labeled checklist of what's complete, missing, or needs the student's action, and can guide the student step-by-step through preparing materials — but never sends anything to an external system without the student's explicit, submission-specific approval.

**Why this priority**: Delivers PRD AC-4, the capstone journey that ties profile, matching, documents, and generation together into an actionable plan; depends on all prior stories for its inputs.

**Independent Test**: Can be fully tested by requesting a plan for a scholarship with known requirements and confirming every checklist item carries one of the defined readiness labels, that missing items are identified, and that no data is transmitted anywhere without a separate, explicit approval step tied to that specific submission.

**Acceptance Scenarios**:

1. **Given** a scholarship with verified requirements and a partially-prepared application, **When** the user requests a plan, **Then** the system returns a checklist where every item is labeled Complete / Missing / User-must-obtain / AI-can-generate / Needs-human-review / Needs-official-verification.
2. **Given** an application plan with missing items, **When** the user asks what remains, **Then** the system lists exactly those items, distinguishing what the student must obtain from what the system can generate.
3. **Given** a fully prepared application package awaiting submission, **When** the student has not yet given explicit approval for that specific submission, **Then** the system does not transmit anything to an external system or address.
4. **Given** the student gives approval for one submission, **When** a different or later submission is prepared, **Then** the earlier approval does not carry forward — a new explicit approval is required.
5. **Given** the guided step-by-step mode and the individual deterministic-button mode are both used for the same scholarship and profile, **When** compared, **Then** they produce the same checklist/readiness result.

---

### Edge Cases

- What happens when a scholarship's deadline has passed by the time it's viewed? → Status must reflect closed/expired, not be presented as open.
- What happens when the same scholarship is discovered from two different sources with different close dates? → Conflict is surfaced or resolved per the deterministic rule (official source wins), never silently averaged or guessed.
- How does the system handle a profile with no hard constraints defined at all? → Matching proceeds on soft/fuzzy criteria only, and the absence of hard constraints is not mistaken for "no criteria to fail."
- What happens when a user submits a URL that is not on/reachable via a governed source at all (e.g., a scam or unofficial aggregator)? → The system does not treat the extracted content as authoritative fact without noting its non-official status.
- What happens when a document upload fails to parse (corrupt file, unsupported format)? → The user is told parsing failed and asked to retry/replace, not silently ignored.
- How does the system respond when asked a question that spans multiple capabilities (e.g., "find and match scholarships for me, then draft my SOP")? → It may sequence the relevant capabilities, but must not guess at an ambiguous single intent.
- What happens when a previously "verified" scholarship record becomes stale (past its freshness window)? → It is flagged as stale and re-verification is attempted before facts from it are presented as current.
- What happens when generation is requested but the selected scholarship's specific requirements haven't been extracted/verified yet? → The system requests verification first rather than generating against assumed generic requirements.

## Requirements *(mandatory)*

### Functional Requirements — Profile (FR-PROFILE)

- **FR-PROFILE-1**: The system MUST let a student create and continuously update a living profile (not a one-time form).
- **FR-PROFILE-2**: The profile MUST support personal/academic info, current and target degree/level, target fields, target countries/regions, nationality, GPA/academic performance, language test status (IELTS/TOEFL/PTE), GRE/GMAT status, work/research experience, publications/projects, funding preferences, and visa/spouse/dependent considerations.
- **FR-PROFILE-3**: The system MUST let the student explicitly classify each profile criterion as a Hard constraint, Soft preference, or Exclusion, and MUST track fields with no provided value as missing/unknown, separately from provided values.
- **FR-PROFILE-4**: Matching MUST consume hard, soft, exclusion, and missing/unknown profile data differently, per the Matching requirements below.

### Functional Requirements — Discovery (FR-DISC)

- **FR-DISC-1**: The system MUST prioritize fresh, official, and broad-geography sources (government, university, providers/foundations, country portals, international organizations, research/fellowship sources, approved connectors) when discovering scholarships.
- **FR-DISC-2**: The system MUST NOT rely on unrestricted, unrestricted-domain web search as its primary discovery strategy.
- **FR-DISC-3**: Discovery MUST operate only within a human-defined, controlled Source Registry that specifies which sources are trusted and under what rules.
- **FR-DISC-4**: Discovery MUST support extraction, normalization, deduplication, official-source verification, freshness tracking, and expired/closed handling of results, plus tracking of which countries/sources have been covered.
- **FR-DISC-5**: A newly encountered source MUST be recorded as a pending candidate requiring human approval and MUST NOT be treated as authoritative until approved.
- **FR-DISC-6**: Discovery MUST support querying along multiple dimensions (country, university, degree level, field, nationality, funding type, intake, requirements, deadlines) rather than a single generic query.
- **FR-DISC-7**: The system MUST NOT claim complete/100% global scholarship coverage; it MUST report coverage as a measured summary (see FR-COV-1).

### Functional Requirements — Scholarship Intelligence (FR-INTEL)

- **FR-INTEL-1**: The system MUST represent, where applicable, each scholarship's name, provider, country, university, program, degree level, field, funding details, tuition/stipend/accommodation/travel/insurance coverage, visa information, spouse/dependent policy, eligibility, nationality restrictions, academic/test requirements, application fee, deadline, intake, required documents, application procedure, official URLs, official source, opening date, and current status.
- **FR-INTEL-2**: Every scholarship field MUST carry a value-status of known / unknown / not_applicable / conditional / conflicting, plus a confidence and provenance reference; no field is assumed present.
- **FR-INTEL-3**: Missing scholarship information MUST be shown as unknown and MUST NOT be guessed or inferred as a default value.
- **FR-INTEL-4**: Every scholarship record MUST carry source provenance and a last-verified date.
- **FR-INTEL-5**: Funding MUST be represented using a benefit-status classification (Fully funded · Substantially funded · Partially funded · Tuition-only · Stipend-only · Other benefit combination · Unknown/not verified) rather than a binary funded/not-funded flag, and MUST NOT apply a single universal percentage threshold for "fully" or "substantially" funded.

### Functional Requirements — Matching (FR-MATCH)

- **FR-MATCH-1**: Matching MUST evaluate hard constraints, soft preferences, exclusions, and unknown/missing information, plus structured eligibility and fuzzy criteria where relevant.
- **FR-MATCH-2**: Matching results MUST be explainable: the user MUST see what matches, what does not, what is unknown, why, and the supporting evidence — never an opaque score alone.
- **FR-MATCH-3**: A hard-constraint failure MUST be treated as disqualifying (not a match), distinct from a soft-preference mismatch, which only lowers match strength/rank.
- **FR-MATCH-4**: Every match result MUST use a fixed, structured verdict: eligibility verdict (Eligible / Likely / Possibly / Not / Unknown-requires-verification), match-strength (Strong / Possible / Not), hard/soft/exclusion breakdown, missing information, and evidence.
- **FR-MATCH-5**: Hard-constraint eligibility facts MUST be decided deterministically and MUST NOT be overridable by model/LLM judgment.
- **FR-MATCH-6**: Matching MUST retain reasoning over soft/fuzzy criteria (not reduce to rules-only) and MUST report criterion-level outcomes of Pass / Fail / Unknown–Needs Verification / Inferred across hard constraints, exclusions, and soft/fuzzy criteria; a hard eligibility failure MUST NEVER be overridden because other factors are strong.

### Functional Requirements — Scholarship URL Matching (FR-URL)

- **FR-URL-1**: The user MUST be able to match a specific, known scholarship by submitting its URL, without first running full discovery.
- **FR-URL-2**: URL matching MUST fetch the official page, extract requirements, verify them, and compare against the profile, producing the same structured verdict as FR-MATCH-4, plus matched criteria, failed criteria, missing information, evidence, and official source.

### Functional Requirements — Q&A (FR-QA)

- **FR-QA-1**: The user MUST be able to ask about eligibility, funding, stipend, accommodation, visa, spouse/dependents, required tests, GPA, documents, deadline, application process, and conditions/exceptions for a given scholarship.
- **FR-QA-2**: Every answer MUST be grounded in verified scholarship source content and returned as a structured, controlled response — not unrestricted generic text generation.
- **FR-QA-3**: When information cannot be verified from sourced content, the answer MUST state "could-not-confirm / Unknown" rather than guessing.

### Functional Requirements — Documents & Generation (FR-DOC, FR-GEN)

- **FR-DOC-1**: The system MUST request missing documents, accept uploads, parse/extract information from them, associate them with the profile/application, compare them against the specific scholarship's requirements, flag detectable inconsistencies, and track readiness.
- **FR-DOC-2**: Supported materials MUST be limited to those the specific scholarship requires (e.g., transcript, degree certificate, CV, Europass CV, SOP, motivation letter, personal statement, study plan, research proposal, recommendation-letter info, certificates, language-test docs, GRE/GMAT docs, portfolio, publications, work-experience docs, financial docs, scholarship-specific forms).
- **FR-DOC-3**: Document requirements MUST be derived dynamically from the specific scholarship/university/program, not assumed uniform across scholarships.
- **FR-GEN-1**: CV/SOP and other generated materials MUST be requirement-aware, following a scholarship/university's specified format or questions where applicable.
- **FR-GEN-2**: Generated materials MUST be grounded strictly in the user's profile and uploaded documents, plus verified scholarship/program requirements; the system MUST NEVER fabricate achievements, experience, publications, employment, grades, or certificates.

### Functional Requirements — Application Planning & Tracking (FR-PLAN, FR-TRACK)

- **FR-PLAN-1**: The user MUST be able to generate a structured application plan for a selected scholarship independent of using other capabilities first.
- **FR-PLAN-2**: The plan MUST turn verified requirements into a labeled checklist using: Complete / Missing / User-must-obtain / AI-can-generate / Needs-human-review / Needs-official-verification.
- **FR-TRACK-1**: The system MUST track saved scholarships, applications, deadlines, missing documents, tasks, and status, and provide a readiness assessment using the FR-PLAN-2 labels.

### Functional Requirements — Application Assistant (FR-APP)

- **FR-APP-1**: The application assistant MUST support the journey: scholarship selected → requirements understood → plan created → materials identified → missing documents requested → gaps identified → materials prepared/reviewed → readiness checked → user approval obtained.
- **FR-APP-2**: The assistant MUST be usable both as a step-by-step guided mode and as individual deterministic actions producing the same result.
- **FR-APP-3**: The system MUST NEVER transmit application materials or data to any external system, portal, or address without explicit, per-submission human approval; an earlier approval MUST NOT be treated as covering a different or later submission. (Live external submission itself is out of MVP scope per FR-APP-4; this requirement governs assistant behavior now and any future submission capability.)
- **FR-APP-4**: Live, autonomous transmission of an application to an external third-party system is out of scope for the MVP; the assistant may draft, assemble, and stage materials for human-approved sending only.

### Functional Requirements — Authentication & Isolation (FR-AUTH)

- **FR-AUTH-1**: The system MUST provide sign up, login, logout, and session/account management.
- **FR-AUTH-2**: Each user's data MUST be isolated from every other user's data.

### Functional Requirements — Source Verification & Coverage (FR-VERIFY, FR-COV)

- **FR-VERIFY-1**: The system MUST follow the flow: human-defined trusted-source boundaries → controlled access → extraction → normalization → deduplication → verification → structured intelligence → matching/Q&A/application use.
- **FR-VERIFY-2**: The system MUST NOT let an LLM freely decide which websites are authoritative; source authority is governed by the Source Registry only.
- **FR-VERIFY-3**: Third-party APIs/aggregators MAY be used as discovery signals, but the official source is the preferred final authority for factual verification.
- **FR-VERIFY-4**: Every scholarship record MUST carry source, source type, official/non-official status, retrieved-at, last-verified-at, current status, deadline, and verification status.
- **FR-VERIFY-5**: The system MUST distinguish record lifecycle states: newly discovered, verified, unverified, updated, expired, closed, reopened, stale, source-unavailable.
- **FR-VERIFY-6**: A failed source retrieval MUST NOT be interpreted or reported as "no scholarship exists"; failures MUST be logged and retried per policy.
- **FR-VERIFY-7**: When sources conflict, the system MUST apply a defined resolution rule (current official source > more-recent > higher-reliability); an unresolved conflict MUST be surfaced explicitly, never silently chosen.
- **FR-COV-1**: The system MUST expose measurable coverage: registered/active/checked/failed sources, last-checked time, countries/universities/providers covered, new candidates, verified opportunities, and coverage gaps.

### Functional Requirements — Structured Output & Intent Routing (FR-OUT, FR-ROUTE)

- **FR-OUT-1**: Responses MUST use structured, intent-specific formats (eligibility, funding, spouse/dependent, application, matching) rather than one universal template or unrestricted free text.
- **FR-OUT-2**: Every factual response MUST include, where applicable, evidence/source reference, verification status, last-verified date, and a next action.
- **FR-OUT-3**: The system MUST avoid presenting unsupported claims as fact, opaque matching scores without explanation, or unverified information as fact.
- **FR-ROUTE-1**: When the system cannot confidently determine the user's intent or the correct capability, it MUST NOT guess or take an action based on an uncertain interpretation.
- **FR-ROUTE-2**: In such cases, the system MUST request clarification from the user before proceeding.
- **FR-ROUTE-3**: When a request legitimately spans multiple capabilities, the system MAY determine and execute an appropriate sequence of actions rather than requiring the user to invoke each one separately.

### Key Entities

- **User Profile**: A living record of a student's academic/personal facts, each criterion tagged Hard / Soft / Exclusion, with an explicit missing/unknown status for unfilled fields; owned by exactly one user.
- **Scholarship Record**: A structured representation of one scholarship opportunity, with per-field value-status (known/unknown/not_applicable/conditional/conflicting), funding classification, source provenance, last-verified date, and lifecycle state.
- **Source Registry Entry**: A governed record of one discovery/verification source, its type, and its status (active / pending-candidate / disabled).
- **Matching Verdict**: The structured output of comparing a Scholarship Record to a User Profile — eligibility verdict, match-strength, per-criterion outcomes (Pass/Fail/Unknown/Inferred), missing information, and evidence references.
- **Q&A Answer**: A structured response to a scholarship-specific question, carrying its confidence label (Verified/Inferred/Unknown), source reference, and last-verified date.
- **Uploaded Document**: A user-supplied file associated with the profile and/or a specific application, with extracted data and a mapping to the requirement(s) it satisfies.
- **Generated Material**: A CV, SOP, or other written artifact produced for a specific scholarship, traceable to the profile/documents that grounded each claim in it.
- **Application Plan**: A per-scholarship checklist of required items, each carrying one readiness label (Complete/Missing/User-must-obtain/AI-can-generate/Needs-human-review/Needs-official-verification).
- **Submission Approval**: A recorded, explicit human approval scoped to one specific application submission; never implied or reused across submissions.

### Assumptions

- The MVP serves a single individual applicant persona (student); multi-user SaaS isolation (FR-AUTH-2) is required from the start, but consultant/agency/institution personas remain explicitly out of scope, per the PRD's resolved decision (OD-7).
- The initial governed Source Registry seed list and exact numeric coverage/freshness/KPI targets are operational configuration set outside this spec (per PRD OD-4/OD-5) and are not defined here.
- "Explicit per-submission human approval" means a distinct, recorded user action tied to one specific application/submission event, not a general consent given once at account setup.
- Live, autonomous transmission of application materials to external portals is deferred to a future capability; the MVP's application assistant prepares and stages materials only.
- Degree levels in scope are Bachelor's, Master's, and PhD; other program types are not explicitly excluded but are not designed for in the MVP.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001 (AC-1)**: A user can submit a real, official scholarship URL and receive an explainable verdict (eligibility, match-strength, per-criterion evidence) plus a correctly-labeled (Verified/Inferred/Unknown) answer to at least one follow-up question, entirely within one working session.
- **SC-002 (AC-2)**: A profile-driven discovery run returns ranked, source-attributed results spanning at least two distinct governed source types, accompanied by a coverage summary (sources checked/failed, gaps) rather than a completeness claim.
- **SC-003 (AC-2)**: 100% of newly encountered sources during discovery are recorded as pending candidates and never used as an authoritative fact source prior to human approval.
- **SC-004 (AC-3)**: A user can upload a required document and see it associated with the correct requirement, then receive a generated CV/SOP in which 100% of factual claims trace back to the user's profile or uploaded documents (zero fabricated facts).
- **SC-005 (AC-4)**: A user can request an application plan for a selected scholarship and receive a checklist in which every item carries exactly one of the six defined readiness labels, with missing items clearly distinguished from ones the system can generate.
- **SC-006 (AC-4)**: Across all tested scenarios, zero application materials are transmitted to an external system without a prior, explicit, submission-specific human approval.
- **SC-007 (AC-5)**: On the verdict-correctness test matrix (known profile × known scholarship → expected verdict), 100% of hard-constraint failures are correctly reported as ineligible, with zero instances of a hard-constraint result being overridden by soft-criteria strength.
- **SC-008 (Groundedness)**: 100% of matching verdicts and Q&A answers presented to users carry a Verified/Inferred/Unknown label, and every fact that cannot be confirmed from sourced content is labeled Unknown rather than stated as fact.
