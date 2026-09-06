# Scholarship AI Assistant — Master Blueprint v1.6

**Status:** Implementation-ready · **Audience:** Claude Code sessions + human developers · **Build method:** Spec-Driven, phase- and session-independent

**v1.1 change theme:** controlled, multi-source, evidence-backed discovery. Adds a governed **Source Registry**, a **multi-source Discovery + Coverage Engine**, a full **data-quality/verification pipeline**, **failure/retry** policy, **continuous monitoring** (future), and **structured response schemas** — **without adding any new agent**. See §29–§35.

**v1.2 change theme (final requirements refinement):** makes product boundaries fully explicit — a **comprehensive scholarship schema with per-field value-status**, a **constraint-typed living profile** (hard / soft / exclusion / missing), **per-capability I/O contracts** for every button (§5.1), an expanded **application material model** (§17), a high-level **future submission architecture** (§37), and **crystal-clear MVP/Post-MVP/Production/Future labels** for every capability (§36). No architecture redesign; no new agents. See §38 for the full change list + consistency audit.

**v1.3 change theme:** Matching upgraded from rule-based service to a **guardrailed agent** — hard constraints remain rule-locked; the agent reasons over soft/fuzzy criteria only. Agent count 4 → **5**. See §7.5 and §38 (v1.3 changelog).

**v1.4 change theme:** adds a **Development Execution Model** (§39–§42) for the Manager (Chat UI) ↔ Worker (CLI) workflow — a **Workstream** layer as the fresh-session boundary, a **context ladder** so no session re-loads the whole blueprint, and concrete **Workstream Brief + Completion Summary** handoff templates. Process only; no architecture change; still 5 agents. See §43 (v1.4 changelog).

**v1.5 change theme:** closes the **Manager → new-Manager-session** continuity gap. Defines a third, distinct handoff artifact — the **Manager Handoff Packet** (planning state carried between Chat UI Manager sessions) — kept separate from the Worker's Completion Summary and the Worker's Workstream Brief. See §42 and §44 (v1.5 changelog).

**v1.6 change theme:** integrates 5 production-grade concepts **without adding any new agent**. *Consolidations:* names the **Agent Loop & Human Gates** pattern (§7.0) and the **Harness** model+constraints+verification frame (§9.1). *New:* **Evals & the Checker** — scored grading with a CI score-regression gate, established early (§25.1, §22 Phase 0/1). *Timing:* a **minimal deployable cloud runtime** lands at end of Phase 2, not Phase 5 (§22, §21). *Excluded:* the **FDE (Forward Deployed Engineer)** positioning material is **positioning, not architecture** — it lives in a separate README/positioning doc, not this blueprint. See §45 (v1.6 changelog).

> **Positioning note (non-architectural):** the **FDE (Forward Deployed Engineer)** positioning material is intentionally **kept out of this technical blueprint** and belongs in a separate README / positioning document. ⚠️ NOT YET DEFINED — needs decision (that positioning doc is not yet written).

---

## 1. Executive Summary

**What we're building.** An AI scholarship research-and-application platform that discovers opportunities, understands a structured user profile, matches scholarships with an *explainable* verdict, researches official sources to answer grounded questions, and prepares application materials (CV, SOP, checklist) — culminating in an approval-gated application case.

**Two objectives, equal weight.**
1. A **working personal tool** for the owner's real scholarship search (single-user MVP, real use — not a throwaway).
2. A **resume-grade demonstration** of Agentic AI engineering: agents where reasoning is required, workflows/services/tools everywhere else.

**Evolution path (no rewrite).** Personal MVP → Production Application → Multi-user SaaS. The architecture makes this path real; it does **not** pull SaaS complexity into the MVP.

**Scope.** Bachelor's, Master's, **and** PhD from day one. Naming and schema stay generic enough to later absorb fellowships, grants, internships, and admissions.

**The disciplined core decision.** Exactly **5 agents** exist (Main Orchestrator, Research/Q&A, Discovery, Application/Submission, Matching). Document processing, CV/SOP generation, application planning, and profile management are deliberately **not** agents — they are deterministic workflows and services with LLM tools for the fuzzy parts. The Matching Agent is **guardrailed**: hard constraints stay rule-locked (§7.5). The Architecture Decision Matrix (§9) enforces this.

**Dual-mode application handling.** The Application/Submission Agent works **step-by-step, asking for missing details and getting explicit approval at each gate** (the agentic mode). The *same* underlying primitives (workflows / services / tools / MCP) are also exposed as **plain buttons** so a user who doesn't want an agent can drive each step manually. One set of logic, two ways to trigger it — this is what makes the platform genuinely agentic *and* deterministic without duplicating code.

**Controlled coverage principle (v1.1).** Discovery does **not** trust one LLM, one search engine, one API, or one aggregator to know every scholarship. Instead a **human-governed Source Registry** defines trusted sources and rules; the Discovery Agent retrieves across **multiple approved channels** at runtime; every important fact is **verified against the official source** with preserved provenance. The goal is **maximum *practical* global coverage — measurable, not mathematically guaranteed.** The system never claims "no scholarship missed"; it exposes *what it has covered* and *where the gaps are*.

---

## 2. Product Scope — MVP vs Production vs SaaS

| Capability | MVP (personal, built first) | Production (hardening) | SaaS (multi-user) |
|---|---|---|---|
| Auth | Signup/Login/Logout, JWT, single user | Refresh tokens, password reset, rate limits | OAuth/social, orgs, RBAC |
| Profile | Structured CRUD + CV-import parser | Validation, versioning | Per-tenant isolation guarantees |
| URL Match | Full flow, explainable result | Caching, retry/backoff | Shared scholarship cache |
| Q&A | Grounded, RAG + live fetch | Answer caching, feedback loop | Quotas per plan |
| Source Registry | Minimal governed registry (seed set, manual approval) | Larger registry, semi-auto validation | Registry admin UI, per-tenant sources |
| Discovery | **Multi-source** (registry-driven: APIs + official + approved search) + rank | More connectors/countries | Fair-use throttling |
| Verification & provenance | Official-source verify + evidence on records | Conflict resolution at scale | Audit exports |
| Coverage engine | Basic per-country/source counts + gaps | Coverage analytics dashboard | Tenant coverage views |
| Source monitoring | **Excluded** (design only) | Scheduled monitoring + alerts | Notifications service |
| Controlled source expansion | Candidate flagging (manual approve) | Semi-automated validation | Governance workflow |
| Documents | Upload, parse, associate | Virus scan, format checks | Object storage + encryption |
| CV / SOP | Generate from real data | Template library, export polish | Usage metering |
| Planner / Tracker | Plan + checklist + readiness | Reminders, deadline alerts | Notifications service |
| App mode (agent + buttons) | Agent orchestrates prepare→review, or manual buttons | Richer step guidance | Team review flows |
| Submission | **Design + approval-gated prep** (no live submit) | Prepare + review, no auto-submit | Approval-gated **assisted live submit** |
| Infra | docker-compose (backend); PostgreSQL via **Neon** + **Qdrant Cloud** (managed from day one, not local containers); local FS for uploads | Observability, backups | Managed DB, autoscale, billing |

**Deliberately excluded from MVP:** actual *live* submission to external portals, **scheduled source monitoring + notifications**, coverage analytics dashboards, semi-automated source validation, multi-tenancy enforcement, billing, reranker (optional flag only), and any microservice split. The Application/Submission **Agent orchestration (prepare + review + approval gates)** and a **minimal governed Source Registry with multi-source discovery + verification** *are* designed and built in MVP; only their production-scale/automation layers are deferred.

---

## 3. Core User Journeys

**Narrative (the target experience):** *"Here is my profile — find scholarships that match me."* → results. *"Tell me everything about this one."* → researched. *"Does it allow my spouse? Does it require GRE?"* → grounded from official sources. *"Match my profile against it."* → explainable verdict + gaps. *"Create the application plan / What documents do you need?"* → checklist. Upload docs → CV + SOP generated from real data → readiness assessment. Submission stays behind explicit approval, far future.

### 3.1 Discovery flow  *(v1.1 — registry-driven, multi-source, verified)*
```
User (NL request + profile)
   │
   ▼
React UI ──► FastAPI ──► Main Agent ──hands off──► Discovery Agent
                                                       │
                          reads ┌────────────── Source Registry (governed) ──────────────┐
                                │   approved sources + rules + reliability + coverage      │
                                └──────────────────────┬───────────────────────────────────┘
                                                       ▼  builds multi-strategy query plan
                          ┌───────────────┬────────────┴────────────┬───────────────┐
                          ▼               ▼                          ▼               ▼
                  Scholarship APIs   Official sources         Approved search   (approved) aggregators
                  (approved)         (gov / univ / provider)  tools             = discovery signal only
                          └───────────────┴────────────┬────────────┴───────────────┘
                                                       ▼
                                              CANDIDATE POOL  (+ source_fetch_log: ok/fail/retry)
                                                       ▼
     DATA-QUALITY PIPELINE:  EXTRACT → NORMALIZE → CLASSIFY → DEDUPLICATE → VERIFY(official) → STORE → INDEX → MATCH
                                                       │ provenance preserved at every step
                                                       ▼
              Store: PostgreSQL (structured facts + provenance) + Qdrant (knowledge vectors)
                                                       ▼
              Matching Service (profile × requirements, explainable)  →  Ranking (deterministic)
                                                       ▼
              Results (+ coverage summary: sources checked / failed / gaps) ──► UI ──► User
```
*Third-party APIs/aggregators are **discovery signals**; the **official source is the final authority** (§32). Failed fetches are logged and retried, never silently treated as "no scholarship" (§33).*

### 3.2 Scholarship URL Matching flow  *(LangGraph workflow, not an agent)*
```
User provides URL ──► FastAPI ──► URLMatch Workflow (LangGraph)
   │
   ├─ node: fetch (MCP/web tool)  ──► raw page/PDF
   ├─ node: extract (LLM structured-output tool) ──► requirements JSON
   ├─ node: normalize (service) ──► canonical Requirement model
   ├─ node: load profile (service) ──► Profile model
   ├─ node: match (Matching service) ──► verdict + criteria breakdown
   ├─ node: gaps (service) ──► missing info / missing docs
   └─ node: attribute (service) ──► source URL + retrieval date
        │
        ▼
   Explainable Result (verdict, matched/failed/unknown, docs, actions)
```

### 3.3 Application preparation flow
```
Scholarship (matched)
   ▼ Requirements → Eligibility (from §17)
   ▼ Application Planner (workflow) → Tasks + Checklist
   ▼ Required Documents identified
   ▼ User uploads docs → Document Workflow (parse/associate/check)
   ▼ Missing items flagged (e.g., character certificate)
   ▼ CV Generation workflow  ─┐
   ▼ SOP/Motivation workflow  ─┤ grounded in real profile only
   ▼ Other written materials  ─┘
   ▼ Case compiled → Readiness Assessment
   ▼ Review → [FUTURE] User Approval → Submit
```

---

## 4. System Architecture Overview

```
┌──────────────────────────── FRONTEND (React + TypeScript) ───────────────────────────┐
│  Auth · Dashboard · Profile · Find · Match-Profile · Match-URL · Q&A · Planner ·      │
│  Documents · CV · SOP · Application Assistant · Tracker      (each = dedicated entry)  │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                         │ REST/JSON (OpenAPI contract)
┌────────────────────────────────────── FastAPI BACKEND (modular monolith) ─────────────┐
│  api/ (routers)  →  orchestration/ (Main Agent)  →  agents/ · workflows/ · services/   │
│                                                                                        │
│  agents/       Main · Research-QA · Discovery · Application/Submission · Matching (OpenAI Agents SDK) │
│  workflows/    url_match · doc_pipeline · cv_gen · sop_gen · app_plan · submit_prep ·    │
│                ingestion(data-quality) · source_validate · source_monitor* (LangGraph)   │
│  services/     profile · normalize · classify · dedup · ranking · auth ·                │
│                source_registry · coverage · verification · conflict_resolution  (Python) │
│  tools/        api_connector · official_fetch · search · web_fetch · pdf_parse ·         │
│                extract · classify · embed   (LangChain + MCP; registry-governed)         │
│  rag/          loaders · chunk · embed · retrieve · rerank · grounding-check             │
│  data/         repositories (Postgres) · vectors (Qdrant) · files (object store)         │
└──────────┬───────────────────────┬────────────────────────┬───────────────────────────┘
           ▼                       ▼                        ▼
   PostgreSQL (structured +   Qdrant (semantic)     File/Object storage (user docs)
   registry + logs + prov.)        │
           │                       │
           └──── MCP + approved connectors (registry-governed: official / gov / univ / API / search) ──── External web
```
*`source_monitor` is designed now, built in a later phase (§22).*

**Walkthrough.** Every request enters FastAPI routers → the **Main Agent** parses intent and dispatches to exactly one of: a specialized agent (Research/Q&A, Discovery, Application/Submission, Matching), a LangGraph workflow, or a service. **Discovery is registry-driven:** the Discovery Agent reads the governed **Source Registry**, builds a multi-strategy query plan, and retrieves across approved connectors; results pass through the **data-quality pipeline** (extract→normalize→classify→dedup→**verify against official source**→store→index→match) with **provenance preserved**. Matching runs as a **guardrailed agent** (§7.5) with hard constraints pre-computed deterministically. Structured facts + provenance persist to Postgres; knowledge chunks to Qdrant; raw uploads to file storage. The Main Agent composes the final, **schema-structured** response.

---

## 5. Frontend Architecture (React + TypeScript)

**Why React+TS, not Streamlit:** the UI must survive the move to SaaS unchanged in foundation — rebuilding a Streamlit UI later is the exact waste we're avoiding. Kept intentionally lean (owner is stronger in Python than TS).

**Stack:** React + TypeScript + Vite · TanStack Query (server state) · lightweight client state (Zustand or Context) · typed API client generated from the backend OpenAPI schema · component lib kept minimal (e.g., shadcn/ui or MUI — decide in Phase 0).

**Screen inventory (each a dedicated capability entry point):**

| Screen | Purpose | Primary backend |
|---|---|---|
| Auth | Signup / Login / Logout | `auth` service |
| Dashboard | Saved scholarships, deadlines, next actions | multiple (read) |
| Profile | Structured profile + CV import | `profile` service |
| Find Scholarships | NL discovery | Discovery agent |
| Match My Profile | Rank stored scholarships vs profile | Matching service |
| Match a Scholarship URL | Paste URL → verdict | `url_match` workflow |
| Scholarship Q&A | Grounded questions | Research/Q&A agent |
| Application Planner | Generate plan + checklist | `app_plan` workflow |
| Documents | Upload / status / gaps | `doc_pipeline` workflow |
| CV Generator | Grounded CV | `cv_gen` workflow |
| SOP / Motivation | Grounded essays | `sop_gen` workflow |
| Application Assistant | Compile case + readiness | Application coordinator |
| Application Tracker | Status across applications | read services |

**Architecture rules:** all data access via the typed API client; no business logic in the frontend; long-running agent/workflow calls use async job pattern (submit → poll/stream status) so the UI never blocks.

### 5.1 Per-Capability Contracts — Input → Processing → Output → Next Action *(v1.2)*

Every major button has an explicit contract. "Processing" names the exact agent/workflow/service (classification in parentheses).

| Capability | Input | Processing | Output | Next action |
|---|---|---|---|---|
| **Find Scholarships** | NL query + profile | Discovery Agent (agent) → registry-driven multi-source → ingestion pipeline → matching | Ranked, verified, source-attributed list + coverage summary | Open one · Match My Profile · Ask Q&A |
| **Match My Profile** | Profile + stored scholarships | Matching service (service) using hard/soft/exclusion (§18) | Per-scholarship match (Strong/Possible/Not) + reasons + evidence | Ask Q&A · Application Planner |
| **Match a Scholarship URL** | Scholarship URL + profile | `url_match` workflow: `official_fetch`→extract→verify→normalize→match | Match verdict · matched/failed criteria · missing info · evidence · official source | Ask Q&A · Application Planner · Documents |
| **Ask Scholarship Q&A** | Question + scholarship context | Research/Q&A Agent (agent): RAG → grounding-check → live `official_fetch` if weak | Intent-schema answer + evidence + official source + last_verified + confidence | Follow-up · Match · Planner |
| **Application Planner** | Chosen scholarship + profile | `app_plan` workflow (workflow) from verified requirements | Labeled checklist (Complete/Missing/User-must-obtain/AI-can-generate/…) + deadlines | Documents · CV/SOP · Application Assistant |
| **Documents** | Uploaded files + requirement set | `doc_pipeline` workflow (workflow) + parse/extract tools | Parsed doc · what it satisfies · gaps · readiness | Upload more · CV/SOP · Assistant |
| **CV Generator** | Profile + docs + target requirements | `cv_gen` workflow (workflow) + `ground_check` | Requirement-aware CV (e.g., Europass if required), grounded only in real data | Edit · attach to application · SOP |
| **SOP / Motivation** | Profile + prompt/questions + requirements | `sop_gen` workflow (workflow) + `ground_check` | Essay answering the university's specified questions, grounded | Edit · attach · Assistant |
| **Application Assistant** | Chosen scholarship + profile + docs | Application/Submission Agent (agent) orchestrating the above with approval gates — **or** buttons in deterministic mode | Compiled case + readiness assessment (labeled) | Fix gaps · human review · (future) submit |
| **Application Tracker** | — | read services (service) | Saved scholarships · applications · deadlines · missing docs · status | Resume any application |

*The same contracts drive the OpenAPI schema, so the frontend types are generated from them.*

## 6. Backend Architecture (FastAPI, modular monolith)

**Why modular monolith:** one deployable, clean internal boundaries, zero microservice overhead in MVP; boundaries chosen so a module can later become a service without refactor.

```
backend/
  app/
    api/            # FastAPI routers (thin; validation + auth + dispatch only)
    orchestration/  # Main Agent + intent routing
    agents/         # research_qa/ , discovery/ , application/ , matching/   (OpenAI Agents SDK)
    workflows/      # url_match/ , doc_pipeline/ , cv_gen/ , sop_gen/ , app_plan/ ,
                    # submit_prep/ , ingestion/ , source_validate/ , source_monitor/ (LangGraph)
    services/       # profile, normalize, classify, dedup, ranking, auth, hard_constraints,
                    # source_registry, coverage, verification, conflict_resolution
    sources/        # connectors (api/official/search) + registry access + fetch-log + retry policy
    tools/          # api_connector, official_fetch, web_fetch, search, pdf_parse, extract,
                    # classify, embed  (+ MCP clients; all registry-governed)
    schemas/        # intent-specific structured response schemas (§34)
    rag/            # loaders, chunk, metadata, embed, retrieve, rerank, grounding
    data/
      repositories/ # Postgres access (SQLAlchemy) — the ONLY place SQL lives
      vectors/      # Qdrant client wrapper
      files/        # object-storage abstraction (local FS → S3 later)
    models/         # Pydantic (API) + ORM (DB) + domain models
    core/           # config, security, logging, errors, deps
  tests/
  migrations/       # Alembic
  specs/            # spec-driven artifacts (see §23)
  adr/              # architecture decision records
```

**Boundary rule:** routers never touch the DB or LLMs directly; they call orchestration/services. Repositories are the only SQL surface. Tools are the only external-call surface. This keeps the module graph acyclic and testable.

---

## 7. Agentic Architecture

Five agents. No sub-agents in MVP (see §8 for when one would be justified). Agents exist only where autonomous reasoning is genuine; everything else stays workflow/service/tool. One agent — Matching (§7.5) — is **guardrailed** because it underpins a fact.

### 7.0 Agent Loop & Human Gates *(consolidation — v1.6; not a new capability)*

The system already runs a **Loop Engineering** pattern; this subsection names it and points to where it lives — it adds nothing new.

- **Loop pattern:** agents run **plan → act → check → re-plan**, with the human stepping in only at explicit **GATES**, not every step. Existing instances: agent reasoning loops (§7 via OpenAI Agents SDK); Research/Q&A **grounding-check → re-research** when context is weak (§7.2, §12); Application/Submission **step-by-step plan→act→check** (§7.4, §17); LangGraph workflows with **checkpoints + human-in-the-loop pauses** (§10).
- **Where the human GATES are (existing only — none invented):**
  - Application/Submission **approval gates** at each prepared step (§7.4, §17).
  - **Final submit gate** — explicit approval before any irreversible submission (§17, §37).
  - **Candidate-source approval gate** — a new source stays `pending` until a human approves it (§29; `source_validate` §10).
  - Any **HITL pause** exposed by a LangGraph workflow (§10).

No new loops or gates are introduced by v1.6; this is a naming/cross-reference consolidation.

### 7.1 Main Scholarship Assistant Agent
- **Purpose:** interpret the user's natural-language request (incl. multi-constraint: *"fully funded IT Master's, GRE not required, spouse allowed"*) and dispatch to the right capability.
- **Responsibility:** intent understanding, constraint extraction, routing, final response composition. It does **not** perform capability work itself.
- **Inputs:** user message + user/profile context. **Outputs:** dispatched call + composed answer.
- **Tools/handoffs:** handoff to Research/Q&A agent, Discovery agent; invoke workflows/services.
- **State:** conversation context (short-lived), current user id.
- **Why an agent:** open-ended NL understanding + dynamic routing across many capabilities is exactly autonomous decision-making; a rule-based router cannot robustly parse compound constraints.
- **Why not a workflow/service:** the branch set and constraint parsing are not deterministic.

### 7.2 Research / Q&A Agent  *(consolidates Q&A + verification + live research)*
- **Purpose:** answer specific scholarship questions **grounded** in reliable sources; verify important claims during discovery.
- **Responsibility:** decide *retrieve-from-knowledge (RAG)* vs *research-live (MCP fetch)*; select sources; check grounding; label Verified/Inferred/Unknown; attach source URLs + dates.
- **Inputs:** question + scholarship/source context. **Outputs:** grounded answer + evidence + confidence label.
- **Tools:** RAG retriever, web_fetch/search via MCP, grounding-check.
- **State:** per-question retrieval trace (for attribution).
- **Why an agent:** it makes a genuine runtime decision — is the answer already grounded in our knowledge base, or must it go to the official site? — and selects tools accordingly. This is dynamic tool selection + Self-RAG reflection, not a fixed pipeline.
- **Why not a workflow:** the retrieve-vs-research branch depends on runtime grounding quality, not a predetermined path.
- **Consolidation rationale:** Q&A, verification, and live research share the same reasoning core (ground a claim against a source). Splitting them into three agents would be needless fragmentation.

### 7.3 Discovery Agent  *(v1.1 — registry-driven, multi-source)*
- **Purpose:** find candidate scholarships that plausibly match the profile by orchestrating retrieval across **approved sources**, not the open web at large.
- **Responsibility:** read the **Source Registry** (§29) for the relevant countries/types; build a **multi-strategy query plan** across systematic dimensions (§30.3); invoke the right **connectors/tools** per source (approved APIs, official/gov/university fetch, approved search); gather candidates into the pool; flag any *newly seen* source as a **candidate source** for governed approval (never auto-trust it); hand candidates to the deterministic **data-quality pipeline** (§31).
- **Inputs:** profile + constraints + registry view. **Outputs:** candidate pool (records + provenance) + candidate-source flags + a coverage summary (sources checked / failed / gaps).
- **Tools:** `api_connector`, `official_fetch`, `search`, `web_fetch` (all registry-governed via MCP where applicable).
- **State:** query plan, sources attempted, fetch results, dedup set.
- **Why an agent:** choosing *which* strategies and connectors to run for *this* profile/country, iterating when a source fails or returns thin results, and triaging candidates is open-ended planning + dynamic tool selection — not a fixed sequence.
- **Why still ONE agent:** all of this is a single reasoning responsibility (plan → retrieve → triage). Per-source agents are unnecessary — each source is a **connector/tool**, and the deterministic pipeline steps are **services/workflows**.
- **Why separate from Research/Q&A:** breadth-first *exploration* (find many) is a different reasoning mode, tool budget, and stopping criterion than depth-first *verification* (confirm one). *Assumption A1: kept separate for clarity and skill demonstration; a single dual-mode Research agent is a viable alternative if agent count must shrink.*

### 7.4 Application / Submission Agent  *(the step-by-step, approval-gated orchestrator)*
- **Purpose:** drive a full application from prepared materials → review → (later) submission, **one step at a time**, pausing to ask the user for any missing detail or document, and requiring **explicit approval at every gate**.
- **Responsibility:** decide the next needed step for *this* scholarship, invoke the right workflow/service/tool/MCP for it (planner, doc pipeline, CV/SOP gen, readiness), detect what's still missing, request it from the user, present each result for approval, and only then move on. For live submission (future phase) it maps profile/documents to the target portal's fields and stops for final approval before sending.
- **Inputs:** target application + user profile/documents + user approvals. **Outputs:** an assembled, approved application case (and, future-phase, a submitted application).
- **Tools/handoffs:** invokes `app_plan`, `doc_pipeline`, `cv_gen`, `sop_gen`, readiness service; uses MCP fetch to read a portal's live requirements; hands specific questions to Research/Q&A.
- **State:** per-application progress (which steps done/approved, what's outstanding) — **persisted** so a paused application resumes across sessions.
- **Why an agent:** the next step is **not fixed** — it depends on what's missing for *this specific* scholarship/portal, what the user supplies, and prior approvals. That is dynamic planning + tool selection + stateful reasoning across an interactive loop — exactly what a workflow can't do, because a workflow's path is predetermined.
- **Why not a workflow alone:** a single fixed graph can't decide *"CV is done and approved, transcript is still missing, so ask for the transcript next, then re-check the portal's current requirement"* — that branching is data- and approval-dependent at runtime.
- **Hard rule:** never submits anything live without an explicit user approval gate (§17, §34-equivalent). MVP builds the orchestration + approvals; live external submission is deferred (§22 Phase 6).

### Dual-mode principle (agentic **and** deterministic)
The Application/Submission Agent is an **optional orchestrator on top of the same primitives**. Every step it performs is also exposed as a **button** that calls the identical workflow/service directly. A user who doesn't want an agent presses buttons and the deterministic path (workflow → LLM tool / MCP / service) does the work; a user who wants hand-holding lets the agent sequence it. **No logic is duplicated** — the agent and the buttons share one implementation.

```
                 ┌──────────────────────┐
   User ───────► │  Main Agent (router) │
                 └───────┬──────────────┘
              handoff    │    invoke
   ┌──────────────┬──────┴───────┬───────────────────────────┐
   ▼              ▼              ▼                           ▼
┌──────────┐ ┌──────────┐ ┌────────────────────┐   workflows / services
│Research/ │ │Discovery │ │ Application/        │   (url_match, matching,
│Q&A agent │ │ agent    │ │ Submission agent    │    cv_gen, planner, ...)
└────┬─────┘ └────┬─────┘ └─────────┬───────────┘        ▲
     │RAG+MCP     │search+MCP        │ invokes SAME ──────┘
     ▼            ▼                  ▼ workflows/tools/MCP + approval gates
 grounding    normalize→...    app_plan · doc_pipeline · cv_gen · sop_gen · readiness
                                     ▲
        Deterministic mode: buttons call these SAME workflows directly (no agent)
```
*The Matching Agent (§7.5) is invoked as a guardrailed step by Main/Discovery/URL-match, not shown as a handoff above.*

### 7.5 Matching Agent (guardrailed)  *(v1.3)*
- **Purpose:** produce an explainable eligibility / match-strength verdict per §18, reasoning over **soft preferences and fuzzy/unstructured criteria** while **hard constraints stay rule-locked**.
- **Responsibility:** read pre-computed **hard-constraint results** (computed deterministically by the `hard_constraints` service **before** the agent runs — never by the agent) + exclusions; reason only over soft preferences and fuzzy criteria (e.g. *"relevant work experience," "research fit"*); assemble the final verdict into the fixed §18 schema.
- **Guardrails (non-negotiable):**
  - **a. Hard-constraint lock:** hard-constraint results (GPA threshold, degree level, nationality, deadline, mandatory tests) are computed by a deterministic pre-step and passed in as **read-only** context. The agent cannot alter, override, or re-derive them.
  - **b. Schema-locked output:** output MUST validate against the fixed §18 verdict object (**Pydantic-enforced**) — no free-text verdicts.
  - **c. Evidence-required:** every matched/failed/unmet criterion the agent produces must carry an evidence/source reference; unsupported claims are **rejected by an output guardrail**.
  - **d. No-guess rule:** any field with `value_status = unknown` (§14) must be surfaced as **missing_information** — the agent may never guess or invent a value.
  - **e. Bounded tool access:** the agent's only tools are matching-relevant (profile reader, requirement reader, fuzzy-criterion resolver) — **no web/search/fetch tools**.
  - **f. Deterministic ranking:** the final ranking formula stays in code (`ranking` service); the agent supplies inputs to it but **never reorders results itself**.
- **Inputs:** profile + scholarship requirements + pre-computed hard-constraint/exclusion results. **Outputs:** the §18 verdict object.
- **Why an agent (despite the hard-fact lock):** reasoning over soft/fuzzy criteria genuinely benefits from LLM judgment (weighing several soft preferences together, assessing loosely-specified "relevant experience") — closer to open-ended judgment than a fixed rule table can express.
- **Why guardrails are mandatory here (unlike the other 4 agents):** Matching underpins a **fact** ("are you eligible"), where an ungoverned agent could contradict a deterministic result. The lock exists specifically to prevent that failure mode.
- **Testing implication:** a fixed test matrix (known profile × known scholarship → expected verdict) runs on **every build**; any hard-constraint guardrail violation is a **build-breaking failure**, not a warning (§25).

```
profile + requirements
        │
        ▼
[ hard_constraints service ]  (deterministic pre-step) ── hard results + exclusions (READ-ONLY) ─┐
                                                                                                 ▼
                                                              ┌──────────────────────────────────────┐
                                                              │  Matching Agent (guardrailed)          │
                                                              │  reasons over soft + fuzzy criteria    │
                                                              │  guardrails: a–f                        │
                                                              └───────────────────┬────────────────────┘
                                                                                  ▼ §18 verdict object (schema-locked)
                                                              [ ranking service ] (deterministic) ─► results
```

---

## 8. When a sub-agent *would* be justified (and why none exist yet)

No sub-agents in MVP. A sub-agent becomes justified only if a parent agent cannot reliably handle a specialized reasoning task within its own loop. Documented future candidates:
- **Discovery → per-source Extraction sub-agent** *only if* extraction across wildly heterogeneous portals needs its own autonomous retry/reasoning beyond a structured-output tool. Until proven, extraction stays a **tool**.
- **Research/Q&A → Source-Ranking sub-agent** *only if* choosing among conflicting official sources needs independent multi-step reasoning. Until then, it's a service heuristic.
- **Application/Submission → per-portal Form-Filling sub-agent** *only if*, at the live-submission phase, individual portals differ so wildly that mapping profile→fields needs its own autonomous reasoning/error-recovery loop. Until proven at Phase 6, field-mapping stays a **tool** the parent agent calls.

Rule enforced: a sub-agent ships only with a written justification of why its parent cannot do the job.

---

## 9. Architecture Decision Matrix

Primary mechanism marked ✅ (secondary support in parentheses in Rationale).

| Capability | Agent | Sub-Agent | Workflow | Service | Tool | Det. Code | Rationale |
|---|:--:|:--:|:--:|:--:|:--:|:--:|---|
| Intent routing | ✅ | | | | | | Open-ended NL + dynamic dispatch |
| Grounded Q&A | ✅ | | | | | | Runtime retrieve-vs-research decision (Self-RAG) |
| Claim verification | ✅ | | | | | | Same reasoning core as Q&A (consolidated) |
| Web discovery (registry-driven, multi-source) | ✅ | | | | | | Autonomous strategy/connector selection + triage |
| Source Registry (governed store) | | | | ✅ | | ✅ | Human-defined data; CRUD + rules, no reasoning |
| Source connectors (API/official/search) | | | | | ✅ | | External access only; one per source type |
| Candidate-source validation/approval | | | ✅ | | | | Ordered check + **human approval gate** (governed) |
| Coverage engine | | | | ✅ | | | Deterministic aggregation over registry + fetch-log |
| Classification | | | | ✅ | (tool) | | Rule/label mapping; LLM tool for fuzzy only |
| Deduplication | | | | ✅ | | ✅ | Deterministic keys + similarity threshold |
| Verification (official-source) | ✅ | | | (svc) | | | Reuses Research/Q&A agent's grounding decision |
| Conflict resolution | | | | ✅ | | | Deterministic policy (official/current preferred) |
| Source monitoring (future) | | | ✅ | | | | Scheduled deterministic re-fetch; no reasoning |
| Structured response schemas | | | | ✅ | | ✅ | Fixed per-intent output contracts |
| Application orchestration (agentic mode) | ✅ | | | | | | Next step depends on runtime gaps + approvals; dynamic planning |
| Application steps (deterministic mode) | | | ✅ | ✅ | | | Same primitives via buttons; no agent needed |
| Live submission (future) | ✅ | | | | (tool) | | Approval-gated; portal-dependent mapping; HITL mandatory |
| URL match | | | ✅ | | | | Fixed ordered steps; no dynamic tool choice |
| Requirement extraction | | | | | ✅ | | LLM structured-output call, single-shot |
| Normalization | | | | ✅ | | | Deterministic mapping to canonical model |
| Matching / eligibility | ✅¹ | | | (svc) | (tool) | | Guardrailed agent: soft/fuzzy reasoning; **hard constraints rule-locked** (§7.5) |
| Ranking | | | | | | ✅ | Deterministic scoring formula |
| Document intake/parse | | | ✅ | | (tool) | | Predictable pipeline; parser is a tool |
| CV generation | | | ✅ | | (tool) | | Predictable, grounded, format-driven |
| SOP / motivation | | | ✅ | | (tool) | | Same as CV |
| Application planning | | | ✅ | | | | Requirements → tasks is structured |
| Readiness assessment | | | | ✅ | | | Deterministic checklist evaluation |
| Profile CRUD | | | | ✅ | | | Plain data management |
| CV-import parsing | | | | | ✅ | | One LLM extraction tool |
| Auth | | | | ✅ | | ✅ | Standard JWT logic |
| Ingestion (RAG) | | | ✅ | | (tool) | | Deterministic ETL pipeline |

**Enforcement:** if a future change proposes turning any ✅Service/Workflow row into an agent, it must first fail the Constraint-1 test in writing.

¹ **Matching (v1.3):** upgraded from Service to a **guardrailed Agent**. It reasons over soft/fuzzy criteria only; hard constraints + exclusions are computed by a deterministic pre-step (`hard_constraints` service) and passed in **read-only** — the agent cannot override them. Ranking stays a deterministic service. Full guardrails a–f in **§7.5**.

### 9.1 Harness Engineering — model + harness *(consolidation + one gap — v1.6)*

Framing: **the system = the model (reasons) + the harness (constrains and verifies).** The harness is mostly already present; this names it and maps it to existing sections.

- **Permission walls (what an agent may do):** bounded per-agent tools — e.g., Matching Agent has no web/search/fetch (§7.5e); source governance restricts fetches to approved sources (§29); authentication/authorization + per-user isolation (§19).
- **Automatic non-LLM checks (deterministic verification of agent output):** Matching hard-constraint lock + schema/evidence/no-guess guardrails (§7.5); `ground_check` nodes that reject fabricated content (§10, §16); verification + conflict-resolution services (§32); source-fetch validation + retry (§33).
- **Sandbox / isolated safe execution *(missing piece — added as a requirement):*** tool actions — and especially **future browser/portal interaction** for assisted submission (§37) — shall execute in an **isolated, safe environment** so a faulty or unexpected action cannot affect the host, user data, or live external systems without passing the harness. This is a control requirement, not a technology choice. ⚠️ NOT YET DEFINED — needs decision (the specific isolation mechanism). *(Docker in §21 is packaging/deployment, not this execution sandbox.)*

No new agent is introduced; the harness is composed of existing guardrails/services/tools plus the sandbox requirement above.

---

## 10. Workflow Architecture (LangGraph)

Workflows are stateful, ordered, branchable, checkpointed, and support human-in-the-loop pauses — but make no *open-ended* tool decisions.

```
url_match:    fetch → extract → normalize → load_profile → match → gaps → attribute → END
doc_pipeline: receive → identify_type → parse → extract_fields → associate → satisfy_check → flag_missing → END
cv_gen:       gather(profile,reqs) → select_format → draft → ground_check(no fabrication) → render → END
sop_gen:      gather → outline → draft → ground_check → polish → render → END
app_plan:     load(reqs,eligibility) → derive_tasks → map_documents → sequence → checklist → END
submit_prep:  compile_case → readiness_check → [APPROVAL GATE] → map_to_portal → [FINAL APPROVAL GATE] → (future) send → END
ingestion:    (per candidate) extract → normalize → classify → deduplicate → verify(official) → store → index → END
              │ any node fail → log to source_fetch_log + retry policy (§33); never drop silently
source_validate: candidate_source → reachability + type + reliability checks → [HUMAN APPROVAL GATE] → add to registry → END
source_monitor*: (scheduled) load approved sources → fetch changes → diff → ingestion → notify* → END   (*future phase)
```

Each node has typed input/output state; failures retry with backoff; `ground_check` nodes hard-block fabricated content. Checkpointing enables resume across sessions (supports phase independence).

**Dual-mode reuse:** these workflows are the shared primitives. In **deterministic mode** a button invokes one workflow directly. In **agentic mode** the Application/Submission Agent (§7.4) decides *which* workflow to invoke next and inserts the approval gates. `submit_prep`'s gates are hard human-in-the-loop stops — the live `send` step is deferred to Phase 6 and never runs without explicit approval.

---

## 11. Tool Architecture

| Tool | I/O contract | Impl |
|---|---|---|
| `api_connector` | (source_id, query) → normalized records + provenance | approved scholarship API client |
| `official_fetch` | (source_id, url) → {content, status, fetched_at, source_url} | registry-governed official/gov/univ fetch (MCP) |
| `web_fetch` | url → {html/text, status, fetched_at} | MCP fetch server |
| `search` | query → [{title,url,snippet}] | MCP search server (approved) |
| `classify` | record → {degree_level, funding_type, provider_type, ...} | rules + LLM tool for fuzzy |
| `pdf_parse` | file → {text, pages, tables} | LangChain loader / PyMuPDF |
| `extract_requirements` | text → Requirement JSON (schema-validated) | LLM structured output (LangChain) |
| `extract_profile` | cv_text → Profile JSON | LLM structured output |
| `embed` | text[] → vector[] | HF sentence-transformer (local) or API |
| `rerank` | (query, docs) → ranked docs | HF cross-encoder (optional flag) |
| `ground_check` | (claim, sources) → {supported: bool, evidence} | LLM + retrieval |

All external calls are confined to tools; every tool returns typed, validated output. LLM extraction tools always emit schema-validated JSON (reject-and-retry on invalid).

---

## 12. RAG Architecture

```
Ingestion → Parse → Chunk → Metadata → Embed → Qdrant
                                                   │
Query → analyze → metadata-filtered retrieve → (optional rerank) → context → LLM → answer + attribution
                                                   │
                                     grounding-check ── weak? ──► trigger live research (MCP)
```

- **Metadata per chunk:** `scholarship_id`, `source_url`, `retrieval_date`, `section_type` (eligibility/funding/family/application), `degree_level`, `country`.
- **Retrieval:** semantic + **metadata filtering** (e.g., only this scholarship's chunks, only "family" sections).
- **Reranking:** HF cross-encoder, **optional** (flag) — enabled only when precision demands it; off by default to control cost/latency.
- **Advanced / Self-RAG — where warranted:** the **grounding-check** loop *is* the Self-RAG justification. If retrieved context doesn't support the answer, the Research/Q&A agent escalates to live official-source research instead of hallucinating. **Where NOT warranted:** simple profile lookups, structured matching — no RAG at all; that's SQL.
- **Qdrant is NOT the source of truth for fresh facts (v1.1).** Vectors hold *knowledge for retrieval*, not authoritative current values. Time-sensitive facts (deadlines, open/closed status, funding) are read from **Postgres structured records** and, when stale or unverified, re-checked **live against the official source** (§32). Indexed knowledge + live discovery are complementary layers, never a substitute for verification.

---

## 13. Vector & Data Storage Boundaries

| Store | Holds | Examples |
|---|---|---|
| **PostgreSQL** | structured business/application data **+ source governance + provenance** | users, profiles, scholarships, requirements, funding, applications, tasks, matches, generated-doc metadata, **source_registry, candidate_sources, source_fetch_log, scholarship_sources (provenance)** |
| **Qdrant** | vector/semantic knowledge | scholarship page/PDF chunks, program descriptions, user-document chunks for retrieval |
| **File/Object storage** | original binaries | uploaded transcripts, certificates, generated CV/SOP files |

Rule: a scholarship's *facts* live in Postgres (queryable, verifiable); its *prose knowledge* for Q&A lives in Qdrant; its *source files* live in object storage. Cross-referenced by `scholarship_id` / `user_id`.

---

## 14. Database Architecture (PostgreSQL — schema-level)

Every user-owned table carries `user_id` (FK) from day one → SaaS isolation is a WHERE-clause guarantee later, not a migration.

```
users(id, email, password_hash, created_at)
profiles(id, user_id, name, nationality, country, current_degree, target_degree_level[BS/MS/PhD/other], updated_at, ...)   -- living, persistent, updateable (§2)
education_records(id, profile_id, degree, field, university, gpa, gpa_scale, start, end)
test_scores(id, profile_id, test_type[IELTS/TOEFL/PTE/GRE/GMAT/other], status[have/planned/none], score, taken_at)
experience(id, profile_id, kind[work/research/publication/project], title, org, detail, start, end)   -- v1.2
-- v1.2 constraint-typed preferences: one row per criterion, typed
profile_criteria(id, profile_id, dimension[degree/funding/field/country/language/test/spouse/...], operator, value,
                 kind[hard_constraint/soft_preference/exclusion], weight, note)   -- drives §18 matching
missing_info(id, profile_id, dimension, reason)   -- info required for a reliable decision but not yet provided (§2)
-- v1.2 comprehensive scholarship model:
scholarships(id, name, provider, country, university_id, program_id, field, degree_level[BS/MS/PhD/other],
             funding_type[full/partial/other], intake, application_fee, application_method, application_procedure,
             official_scholarship_url, official_application_url,
             lifecycle_status[newly_discovered/verified/unverified/updated/expired/closed/reopened/stale/source_unavailable],
             opening_date, deadline, closing_status, conditions, exceptions, notes,
             retrieved_at, last_verified_at, verification_status)
universities(id, name, country)
programs(id, university_id, name, field, duration, intake)
-- per-field value with status, so "unknown" is never guessed (§1):
scholarship_fields(id, scholarship_id, key, value,
                   value_status[known/unknown/not_applicable/conditional/conflicting],
                   confidence[verified/inferred/unknown], source_id, evidence_snippet, last_verified_at)
requirements(id, scholarship_id, category[eligibility/academic/gpa/language/test/age/experience/nationality/research/...],
             key, value, mandatory, value_status[known/unknown/not_applicable/conditional/conflicting],
             confidence[verified/inferred/unknown])
funding_details(id, scholarship_id, tuition_coverage, tuition_amount_or_pct, stipend, accommodation, travel_airfare,
                health_insurance, visa_support, family_dependent_benefits, spouse_allowed, dependent_policy,
                value_status, confidence)
scholarship_sources(id, scholarship_id, source_id, url, retrieved_at, verified_at, verification_status[verified/unverified/conflicting], reliability, evidence_snippet)   -- per-scholarship provenance
-- v1.1 source governance:
source_registry(id, name, organization, country, region, source_type[gov/univ/provider/foundation/embassy/api/aggregator/...], official_status, domain, access_method[api/mcp/web], discovery_role, verification_role, reliability_level, update_frequency, status[active/failing/disabled], extraction_rules, constraints, last_checked_at, last_success_at, notes)
candidate_sources(id, discovered_from, url, proposed_type, signals, status[pending/approved/rejected], reviewed_by, reviewed_at)   -- controlled expansion (§29)
source_fetch_log(id, source_id, started_at, status[ok/fail/timeout], http_status, error, retry_count, items_found)   -- coverage + health + retry (§33)
applications(id, user_id, scholarship_id, status)
application_documents(id, user_id, application_id, type, file_ref, parsed_meta, satisfies_requirement_id)
generated_documents(id, user_id, application_id, type[CV/SOP/motivation], file_ref, generated_at)
tasks(id, application_id, description, category, status, due_date)
matches(id, user_id, scholarship_id, verdict, matched[], failed[], unknown[], missing_docs[], created_at)
audit_log(id, user_id, action, entity, at)
```

Full DDL + Alembic migrations are Phase-0/1 artifacts.

**Value-status discipline (v1.2):** no scholarship contains every field. Each field/requirement/funding value carries a `value_status` — `known · unknown · not_applicable · conditional · conflicting` — plus a `confidence`. **Missing information is stored as `unknown`, never guessed or invented** (§1, §32). A `conflicting` value keeps both sources for resolution (§32). The profile mirrors this: `missing_info` records what's needed for a reliable decision but not yet supplied.

---

## 15. MCP Architecture

**Justified role:** live retrieval of **current official-source** content that must not be answered from stale LLM memory (e.g., *"What is the CSC China accommodation policy?"*, or a deadline).

```
Research/Q&A agent ─┐                    ┌─ [ official_fetch server ] ─► gov / univ / provider (official)
Discovery agent    ─┤─► MCP + connectors ─┤─ [ web_fetch server ]      ─► approved web
                    │  (registry-governed) ├─ [ search server ]        ─► approved search (signal)
                    │                      └─ [ api_connector ]        ─► approved scholarship APIs
Application agent  ─┘
```

- **Registry-governed (v1.1):** connectors act **only** on sources/rules defined in the Source Registry (§29). The LLM does **not** freely decide which domains are authoritative — governance decides; the agent chooses *among approved* sources.
- **What flows through:** a `source_id` + URL/query out; typed `{content, status, fetched_at, source_url}` back — retrieval date + source_id captured for provenance.
- **Not MCP:** internal DB/vector access, matching, generation — in-process.
- MCP + connectors exist because official-source freshness + multi-channel coverage are hard product requirements (§32, §30), not to say "we use MCP."

---

## 16. Document, CV & SOP Subsystems

**Documents (`doc_pipeline` workflow) — document intelligence (v1.2):** ask the user for missing documents → accept uploads → parse/extract (pdf_parse + extraction tool) → extract relevant info → associate to profile/application → compare against the specific requirement → **detect missing info and inconsistencies where possible** → track readiness → tell the user exactly what remains. Stays a **workflow + tools**, not an agent hierarchy.

**CV / SOP / written material (`cv_gen` / `sop_gen` workflows) — requirement-aware (v1.2):** never generic. If a university specifies a CV format/content → follow it; if **Europass** is required → generate to that spec; if the university specifies **SOP/motivation questions** → answer those exact questions. Grounded strictly in **user profile + user-provided documents + verified scholarship/university/program requirements**. The `ground_check` node **rejects any content not traceable to real data** — never fabricates achievements, experience, publications, employment, grades, or certificates.

---

## 17. Application Preparation & Tracker

Planner turns requirements → tasks → checklist → readiness. Every item is labeled:

`Complete · Missing · User-must-obtain · AI-can-generate · Needs-human-review · Needs-official-verification`

### 17.1 Long-term Application Assistant workflow *(v1.2)*
```
Select scholarship → understand requirements → create plan → identify required materials →
request missing info/docs from user → analyze supplied docs → identify remaining gaps →
prepare materials → validate vs scholarship/university requirements → compile package →
readiness check → human review → [FUTURE] submission (§37)
```

### 17.2 Application material model *(v1.2)*
Requirements are **derived dynamically from the specific scholarship/university/program** — not every scholarship needs every material. Supported when required: transcript · degree certificate · passport/ID info (where appropriate) · CV · **Europass CV** · SOP · motivation letter · personal statement · study plan · research proposal/plan · recommendation-letter guidance + recommender info · certificates · language-test documents · GRE/GMAT documents · portfolio · publications · work-experience documents · character certificate · financial documents · scholarship-specific forms · university-specific forms · other written materials. Each material maps to a `requirement` and carries a readiness label.

Tracker records saved scholarships, applications, deadlines, missing docs, tasks, status, (future) interview stage and results.

### Two modes over the same primitives
- **Agentic mode — Application/Submission Agent (§7.4):** the user says *"prepare my application for this scholarship."* The agent works step-by-step — checks what's missing, **asks the user for each missing detail/document**, runs the right workflow (plan → documents → CV → SOP → readiness), **presents each result for approval**, and only then advances. Progress is persisted, so it resumes later.
- **Deterministic mode — buttons:** the user who doesn't want an agent presses **Application Planner**, **Documents**, **CV Generator**, **SOP**, **Readiness** individually. Each button calls the *same* workflow/service directly. No agent in the loop, identical output.

Both modes converge on the same case object and the same labels:
`Complete · Missing · User-must-obtain · AI-can-generate · Needs-human-review · Needs-official-verification`

### Submission — always approval-gated
Flow is invariant in both modes: **Prepare → Review → explicit user approval → (future) Submit.** The agent may *assemble and map* the submission, but the **live send to any external portal is deferred to Phase 6 and never executes without an explicit final approval.** No autonomous submission, ever.

---

## 18. Matching Engine (explainable)

**Two layered verdicts (v1.2):**
- **Eligibility verdict:** `Eligible · Likely Eligible · Possibly Eligible · Not Eligible · Unknown-requires-verification`.
- **Match-strength verdict:** `Strong Match · Possible Match · Not a Match` — derived from how the scholarship scores against the profile's **constraint types**.

The engine reads `profile_criteria` (§14) and treats the three kinds **differently**:

| Criterion kind | Effect on result |
|---|---|
| **Hard constraint** (e.g., Master's, fully funded, field=IT) | A failure → **Not a Match** (dominates). Must be satisfied. |
| **Soft preference** (e.g., Europe preferred, spouse-friendly) | A miss **lowers rank/strength** but never disqualifies. |
| **Exclusion** (e.g., GRE required, IELTS required) | If the scholarship *has* the excluded attribute → **Not a Match**. |
| **Missing info** | Criterion can't be evaluated → surfaced as **missing information**, never guessed. |

Output object (no opaque percentage):
```
{ eligibility_verdict, match_strength,
  hard_constraints[]{criterion, result:pass/fail/unknown},
  soft_preferences[]{criterion, result:met/unmet},
  exclusions_triggered[],
  matched_criteria[], failed_criteria[], missing_information[],
  unverified_criteria[], required_documents[], remaining_actions[], evidence[] }
```
Deterministic rules evaluate structured criteria (nationality, GPA threshold, GRE mandatory?, deadline open?, degree level). An LLM resolves only *fuzzy/unstructured* criteria (e.g., "relevant work experience"), labeled `inferred` until verified. A **hard-constraint failure is categorically different from a soft-preference mismatch**. This verdict is produced by the **Matching Agent (guardrailed) — see §7.5**: hard constraints + exclusions are computed by a deterministic pre-step and passed to the agent as read-only, so the agent reasons over soft/fuzzy criteria only and can never override a hard fact. Output is schema-locked to the object above.

---

## 19. Security & Privacy

Auth · authorization · **user isolation (every query scoped by `user_id`)** · secure file handling · secrets via env/secret manager · API-key protection · input validation (Pydantic) · access control · structured logging · least-privilege DB. MVP-appropriate now; the `user_id` discipline makes SaaS isolation enforceable without rework. One user's data must never be reachable by another — enforced at the repository layer.

---

## 20. Authentication Design

MVP: email/password, hashed (bcrypt/argon2), JWT access tokens, Signup/Login/Logout endpoints, protected routes via FastAPI dependency. Structured so Production adds refresh tokens + reset, and SaaS adds OAuth/orgs/RBAC — without touching the isolation model.

---

## 21. Technology Justification Table

| Tech | Where | Why | Alternative rejected |
|---|---|---|---|
| React + TypeScript | Frontend | SaaS-durable UI foundation | Streamlit (would require full rebuild) |
| FastAPI | Backend | Async, typed, OpenAPI, Python-native | Django (heavier than needed) |
| OpenAI Agents SDK | 5 agents | Agent loop + handoffs + tool calling + interactive approval steps; **output guardrails** enabled for the Matching Agent (schema-lock, evidence-required, no-guess, hard-constraint read-only) | Hand-rolled loops (more error-prone) |
| LangGraph | Workflows | Stateful, checkpointed, HITL branches | Ad-hoc orchestration (no resumability) |
| LangChain | Tools/RAG glue | Loaders, structured output, retrievers | Reinventing loaders/parsers |
| Qdrant | Vectors | Metadata-filtered semantic search | pgvector (weaker filtering ergonomics at scale) |
| PostgreSQL | Structured data | Relational integrity for verifiable facts | NoSQL (loses relational guarantees) |
| MCP | Live fetch/search | Fresh official-source retrieval | Static LLM memory (stale, unsafe) |
| Hugging Face | Embeddings + rerank | Local, cost-controlled, demonstrable | API-only embeddings (cost/lock-in) |
| Docker | All services | Reproducible dev/prod parity | Bare-metal setup |

*Deployment timing (v1.6, updated):* Docker packages the **backend** app (local parity from Phase 0). The **managed-DB/vector** part of the "leave the laptop early" goal was pulled forward: **PostgreSQL (Neon)** and **Qdrant Cloud** are both managed cloud services from **Phase 0**, not local containers — this partially resolves the earlier ⚠️ NOT YET DEFINED provider choice for DB/vector. A **minimal cloud runtime for the backend app itself** (containerized app + public endpoint + managed object storage) still lands at **end of Phase 2** (§22); **full production hardening = Phase 5**, **SaaS-scale managed infra = Phase 6**. Backend-hosting provider + object storage: ⚠️ NOT YET DEFINED — needs decision.

---

## 22. Phase / Tier Plan

Each phase ends with the repo in a **stable, contract-defined state**. Per phase, a fresh Claude Code session needs only: **Master Blueprint + that phase's spec + repo state + named contracts** — never chat history. *Within a phase, work is decomposed into **Workstreams** (§40) — the practical fresh-session boundary for the Manager/Worker workflow (§39).*

**Ordering rationale:** profile is prerequisite for everything; URL-match + Q&A deliver real personal value with the least infrastructure (no autonomous discovery); discovery reuses Phase-1 extraction+matching primitives; documents/generation depend on profile + matched scholarships; the Application/Submission Agent depends on *all* those primitives existing first (it orchestrates them), so it lands in Phase 4; live external submission is riskiest and last (Phase 6).

### Phase 0 — Foundation
- **Objective:** running skeleton + auth + contracts + eval harness scaffold.
- **Scope:** docker-compose for the FastAPI backend, connecting to **PostgreSQL via Neon** and **Qdrant Cloud** as managed services (no local Postgres/Qdrant containers; React deferred to Phase 1), auth (signup/login/logout, JWT), base schema + Alembic, OpenAPI contract, CI, `specs/` + `adr/` scaffolding; **eval-harness scaffold + CI score-gate wiring (§25.1)** — empty/placeholder suites now, real evals attach in Phase 1.
- **Outputs/contracts:** OpenAPI v0, DB schema v0, module skeleton, ADR-000..00x, **eval-suite + CI-gate skeleton**.
- **Tests:** auth unit + API, migration test, health checks; **CI runs the (initially trivial) eval gate**.
- **Acceptance/DoD:** user can sign up/log in/log out; `docker-compose up` runs the stack; CI green **with the eval score-gate active**.
- **Fresh-session artifacts:** blueprint + Phase-0 spec + empty repo.

### Phase 1 — Profile + URL Match + Q&A *(usable personal tool)*
- **Objective:** owner can maintain a profile, match a known scholarship URL, and ask grounded questions.
- **Scope:** profile CRUD + CV-import tool; `url_match` workflow (fetch→extract→normalize→match→gaps→attribute); Research/Q&A agent + RAG + MCP fetch; `hard_constraints` service + **Matching Agent (guardrailed)** + explainable result; Qdrant ingestion for fetched pages.
- **Outputs/contracts:** Requirement/Profile/Match models frozen; RAG collection schema; agent/tool interfaces; endpoints for profile, url-match, q&a.
- **Tests:** matching verdict-correctness matrix (known profile × known scholarship → expected verdict); **guardrail violation tests (hard-constraint override attempt must fail the build)**; schema-lock + evidence-required + no-guess output tests; extraction schema tests, RAG retrieval test, grounding test (no-hallucination), URL-match e2e, agent tool-call tests; **first real evals attached to the score-gate (§25.1): matching-correctness and Q&A-groundedness scores + checker-accuracy check**.
- **Acceptance/DoD:** paste a real scholarship URL → explainable verdict + gaps; ask "does it require GRE?" → grounded answer with source URL + Verified/Inferred/Unknown label.
- **Fresh-session artifacts:** blueprint + Phase-1 spec + repo (with Phase-0 contracts) + model/contract files.

### Phase 2 — Governed multi-source Discovery + verified ranked matching  *(v1.1 expanded)*
- **Objective:** "find scholarships that match me" across **approved sources**, verified and source-attributed, with a **coverage summary**.
- **Scope:** **Source Registry** (§29) + seed set of carefully researched sources (a few countries) + candidate-source flagging with **manual approval**; **multi-source Discovery Agent** (§30) using `api_connector` / `official_fetch` / `search`; **data-quality pipeline** `ingestion` (extract→normalize→classify→dedup→**verify(official)**→store→index→match, §31); **coverage service** (basic counts + gaps, §30.4); **verification + conflict-resolution services** (§32); **failure/retry** via `source_fetch_log` (§33); ranking; semantic search (Qdrant).
- **Contracts:** `source_registry` / `candidate_sources` / `source_fetch_log` / `scholarship_sources` schemas; connector interface; candidate-source schema; ranking formula; coverage summary schema.
- **Tests:** registry CRUD + governance (candidate not auto-trusted); connector contract tests (mocked); dedup + classify determinism; verification grounding; conflict-resolution policy; retry/fail logging (failed fetch ≠ "none found"); coverage counts; ranking determinism.
- **DoD:** profile-driven discovery across ≥2 source types returns **ranked, verified, source-attributed** results **plus** a coverage summary (checked/failed/gaps); a discovered new source lands as a *candidate* requiring approval, never auto-authoritative.
- **Leaving-the-laptop (end of Phase 2 — v1.6):** deploy a **minimal cloud runtime** — the containerized app with a **public endpoint**, connected to **managed DB / vector / object storage** — so the product runs independently of the local machine. This is a minimal deployable target only; full production hardening stays Phase 5 and SaaS-scale managed infra stays Phase 6. Concrete provider/services: ⚠️ NOT YET DEFINED — needs decision.
- **Fresh-session artifacts:** blueprint + Phase-2 spec + repo (Phase 0–1 contracts).

### Phase 3 — Documents + CV/SOP
- **Scope:** `doc_pipeline`, requirement-satisfaction checks, missing-item detection; `cv_gen` + `sop_gen` with `ground_check`.
- **Tests:** parse/associate, satisfaction logic, anti-fabrication tests, format-compliance.
- **DoD:** upload transcript → associated + satisfies requirement; generate CV/SOP grounded in real data only.

### Phase 4 — Planner + Tracker + Application/Submission Agent (dual-mode)
- **Objective:** end-to-end application assembly, both via buttons and via the step-by-step agent — up to an approved, ready case (no live external submit yet).
- **Scope:** `app_plan` + `submit_prep` workflows, checklist labeling, tracker, readiness; **Application/Submission Agent** orchestrating those workflows with per-step detail requests and approval gates; buttons exposing the same workflows deterministically.
- **Contracts:** application-case model, per-application progress/state model, approval-gate contract.
- **Tests:** agent step-sequencing (asks for missing item, waits for approval), workflow parity (button vs agent produce same case), readiness correctness, approval-gate enforcement (nothing advances past a gate without approval).
- **DoD:** "prepare my application" → agent walks steps, requests missing docs, gets approvals, outputs a ready case; the same steps also work as standalone buttons.
- **Fresh-session artifacts:** blueprint + Phase-4 spec + repo (Phase 0–3 contracts).

### Phase 5 — Production hardening + source monitoring & coverage analytics
- **Scope:** observability, rate limits, retries/backoff, security hardening, backups, deployment config; **`source_monitor` workflow** (scheduled re-fetch of approved sources → diff → ingestion → alerts, §35-Monitoring); **source-health monitoring**; **coverage analytics** (dashboards over `source_fetch_log`); **semi-automated candidate-source validation** (still human-approved).
- **DoD:** approved sources are re-checked on a schedule; new/changed/closed scholarships are picked up; coverage + source health are visible; stale records flagged.

### Phase 6 — SaaS + live assisted submission
- **Scope:** multi-tenancy enforcement, billing, plans, usage/quotas, notifications, managed infra; **wider source coverage + more connectors/countries**; per-tenant sources; **live approval-gated assisted submission** (portal field-mapping tool, final-approval gate; optional per-portal form-filling sub-agent only if justified per §8).

---

## 23. Spec-Driven Development Model

**Specs are executable artifacts, not after-the-fact docs.** They live in `specs/` in the repo:
```
specs/
  blueprint/           # this document (v1.0)
  phase-0/ ... phase-6/ # per-phase spec: objective, scope, contracts, acceptance
  contracts/           # OpenAPI, JSON schemas (Requirement, Profile, Match, ...)
  adr/                 # architecture decision records
```
Flow: **Requirement → Spec → Design → Implementation → Tests → Acceptance → Verification.** Each acceptance criterion traces to a requirement id; each GitHub issue cites its spec section; implementation PRs reference the issue + spec. A phase is "done" only when its spec's acceptance table is fully green.

---

## 24. GitHub Issue Decomposition

Model: **Phase → Milestone → Epic → Issue → Task → Test → Acceptance Criteria.** Every issue is self-contained (no prior conversation needed).

**Example Issue 1 — `[P1][url-match] Requirement extraction tool`**
- **Objective:** extract normalized requirements from fetched scholarship page text.
- **Scope:** implement `extract_requirements` tool (LLM structured output → schema-validated `Requirement[]`).
- **Dependencies:** Phase-0 `Requirement` model; `web_fetch` tool.
- **Files:** `tools/extract.py`, `models/requirement.py`, `tests/tools/test_extract.py`.
- **Behavior:** given page text, return schema-valid `Requirement[]` with `category`, `mandatory`, `confidence`; invalid JSON triggers one retry then error.
- **Tests:** schema validation; known-page fixture → expected fields; malformed-input handling.
- **Acceptance/DoD:** ≥N fixtures pass; 100% outputs schema-valid; no network in unit tests (mocked).

**Example Issue 2 — `[P1][matching] Explainable matching service`**
- **Objective:** produce the §18 verdict object from Profile × Requirement[].
- **Files:** `services/matching.py`, `models/match.py`, `tests/services/test_matching.py`.
- **Behavior:** deterministic rules for hard criteria; LLM tool only for fuzzy, output labeled `inferred`; never returns bare percentage.
- **Acceptance/DoD:** verdict correct on fixture matrix (eligible/failed/unknown); every criterion categorized; missing docs listed.

**Example Issue 3 — `[P1][qa] Grounded Q&A endpoint`**
- **Objective:** answer a scholarship question grounded via RAG, escalating to live fetch when context is weak.
- **Files:** `agents/research_qa/`, `rag/`, `api/qa.py`, `tests/agents/test_qa_grounding.py`.
- **Behavior:** retrieve → grounding-check → if weak, MCP fetch → answer with source URL + date + Verified/Inferred/Unknown.
- **Acceptance/DoD:** answers cite a source; unsupported claims labeled Unknown, never fabricated; grounding test passes.

**Example Issue 4 — `[P2][sources] Source Registry + governed access`**
- **Objective:** governed store of trusted sources with rules + reliability, and the only access path connectors may use.
- **Scope:** `source_registry` table + `source_registry` service (CRUD, filter by country/type, mark status); connectors must resolve a `source_id` before fetching.
- **Files:** `services/source_registry.py`, `sources/registry.py`, `models/source.py`, migration, `tests/services/test_source_registry.py`.
- **Behavior:** connectors reject any fetch whose `source_id` isn't `active` in the registry; discovered new sources are written to `candidate_sources` as `pending`, never used as authoritative.
- **Tests:** CRUD; filter; governance (candidate not usable until approved); disabled source blocks fetch.
- **Acceptance/DoD:** seed registry loads; a `pending` candidate cannot be fetched-as-authoritative; approval promotes it to `active`.

**Example Issue 5 — `[P2][discovery] Multi-source ingestion pipeline with provenance + retry`**
- **Objective:** turn a candidate pool into verified, stored, source-attributed scholarships.
- **Scope:** `ingestion` workflow (extract→normalize→classify→dedup→verify→store→index); write `scholarship_sources` provenance; log every fetch to `source_fetch_log` with retry policy.
- **Files:** `workflows/ingestion/`, `services/{normalize,classify,dedup,verification,conflict_resolution}.py`, `tests/workflows/test_ingestion.py`.
- **Behavior:** a failed/timed-out source is logged + retried per policy and surfaced as a coverage gap — **never** silently dropped; conflicting facts resolved by policy (official/current preferred); unverifiable facts stored as `unverified`, not guessed.
- **Tests:** dedup determinism; classify labels; verification sets status; conflict policy; failed fetch → logged + retried + gap (not "none found").
- **Acceptance/DoD:** pipeline produces verified records with provenance; coverage summary reflects checked/failed/gaps; anti-hallucination tests pass.

---

## 25. Testing Strategy (mapped to phases)

| Test type | Where | Phase |
|---|---|---|
| Unit | services, tools, models | all |
| API | routers/endpoints | 0+ |
| Agent | tool-calls, handoffs, routing | 1,2 |
| Tool | extraction/parse contracts | 1+ |
| RAG/retrieval | recall + metadata filter | 1,2 |
| Matching | verdict correctness matrix + **guardrail enforcement (hard-constraint lock never overridden)** | 1 |
| Document | parse/associate/satisfy | 3 |
| Anti-hallucination | grounding + no-fabrication + conflict policy | 1,2,3 |
| Source governance | candidate-not-auto-trusted, disabled blocks fetch | 2 |
| Connector contracts | per-source I/O, mocked | 2+ |
| Ingestion / data-quality | classify, dedup, verify, provenance | 2 |
| Failure/retry | failed fetch → logged + retried, not "none" | 2,5 |
| Coverage | counts + gaps correctness | 2,5 |
| Response schemas | per-intent schema conformance | 1,2 |
| Monitoring | scheduled re-fetch + diff | 5 |
| E2E | url-match, discovery, app-prep | 1,2,4 |
| Security | authz, isolation, input validation | 0,5 |

### 25.1 Evals & the Checker *(NEW — v1.6)*

**Distinct from §25 Testing.** Tests are **pass/fail** on fixed cases. **Evals are a scored grading system** for agent outputs, and they gate change:

- **(a) Outputs are graded.** Agent outputs get quality **scores**, not just pass/fail — reusing existing targets: Matching verdict correctness (§7.5, §25), Q&A groundedness/evidence (§25, §34), extraction & verification quality (§31, §32).
- **(b) The checker is itself graded.** The grader/checker's own accuracy is measured against a trusted labeled set, so we can **trust the checker** before trusting its scores.
- **(c) Score-regression gate (CI).** Every code change runs the eval suite; **a change that lowers scores is blocked** in CI. The existing **build-breaking guardrail rule** (hard-constraint lock, §7.5, §25) becomes one **input** to this gate — the gate is broader (scores), the guardrail is absolute (hard fail).
- **Relationship:** per-phase tests (§25) stay as-is; **evals sit above them** as a scored quality layer + merge gate.
- **Timing:** the eval harness + CI score-gate are **established early (Phase 0/1)**, not deferred — so quality is measured from the first agent capability onward (see §22 Phase 0 & Phase 1).
- **Thresholds/targets:** ⚠️ NOT YET DEFINED — needs decision (minimum scores, regression tolerance, checker-accuracy bar).

No new agent; evals are a verification/CI concern (harness §9.1), implemented as a suite + gate.

---

## 26. MVP → Production → SaaS Evolution

- **After MVP (Production):** observability, retries/backoff, caching, rate limits, backups, security hardening, deployment.
- **SaaS (separate future phase):** multi-tenancy enforcement, user isolation guarantees, production auth (OAuth/RBAC), billing, subscription plans, quotas, monitoring/analytics, notifications, and **live approval-gated assisted submission** (the Application/Submission Agent's orchestration + approvals ship earlier in Phase 4; only the live external send is here).
- **Kept out of MVP on purpose** so the first version stays small in scope, strong in architecture.

---

## 27. Assumptions & Open Decisions

- **A1:** Discovery and Research/Q&A kept as separate agents; mergeable into one dual-mode agent if agent count must shrink.
- **A2:** Embeddings default to a local HF model; swap to API embeddings is a config change.
- **A3:** Reranker off by default (flag) to control cost/latency.
- **A4:** Component library (shadcn/ui vs MUI) decided in Phase 0.
- **A5:** MCP servers = a web-fetch + a search server; specific providers chosen in Phase 1.
- **A6:** File storage = local FS in MVP, S3-compatible interface for later.
- **A7:** Async job pattern (submit + poll/stream) for long agent/workflow calls.
- **A8:** Application/Submission is one agent orchestrating shared workflows in agentic mode, with the same workflows exposed as deterministic buttons. Live external submission is Phase 6; a per-portal form-filling sub-agent is added only if portals prove too heterogeneous for a tool.
- **A9 (v1.1):** Discovery is registry-driven and multi-source but stays **one agent**; each source is a connector/tool, each pipeline step a service/workflow. No per-source agents.
- **A10 (v1.1):** Source approval is **human-governed** — MVP uses manual approval of candidate sources; semi-automated validation (still human-confirmed) is a later phase. The LLM never self-authorizes a source.
- **A11 (v1.1):** MVP seeds a **small, carefully researched registry** (a few countries/types) — not an exhaustive world list. Coverage grows by adding registry entries, not by rewriting architecture.
- **A12 (v1.1):** Coverage is reported as *measured* (checked/failed/gaps), never asserted as complete.
- **A13 (v1.3):** Matching is a **guardrailed agent**, not a pure rule-based service. Hard constraints and exclusions are computed deterministically and passed to the agent as locked, read-only context; the agent reasons only over soft preferences and fuzzy criteria. This trades a small amount of determinism for reasoning quality on ambiguous criteria, guarded against by schema-lock, evidence-required, and no-guess output guardrails plus a mandatory hard-constraint regression test matrix (§25).

---

## 28. Requirements Coverage Audit

| Check | Status | Where |
|---|:--:|---|
| Every major requirement covered | ✅ | whole doc |
| No user workflow missing | ✅ | §3, §5 |
| No unnecessary agents / sub-agents | ✅ (5 agents, each justified) | §7–9 (matrix) |
| No unjustified technology | ✅ | §21 |
| React+TS, no Streamlit main UI | ✅ | §5 |
| FastAPI modular monolith | ✅ | §6 |
| Qdrant / Postgres / files boundaries explicit | ✅ | §13 |
| RAG proper; MCP justified | ✅ | §12, §15 |
| Auth + authorization + user isolation | ✅ | §19, §20 |
| BS / MS / PhD supported | ✅ | §2, §14 |
| Discovery, URL-match, profile-match, Q&A, official research | ✅ | §3,7,15,18 |
| Documents + CV/SOP grounded (no fabrication) | ✅ | §16 |
| Planning + preparation + tracker | ✅ | §17 |
| Application/Submission agent (step-by-step, asks details, approval-gated) | ✅ | §7.4, §17, §22 |
| Dual-mode: agentic **and** deterministic button-driven (no duplicated logic) | ✅ | §7.4, §10, §17 |
| Future live submission w/ mandatory human approval | ✅ | §17, §22, §26 |
| Phase independence; specs as artifacts | ✅ | §22, §23 |
| GitHub issue decomposition + Phase-1 examples | ✅ | §24 |
| MVP → Production → SaaS as separate future work | ✅ | §26 |
| Anti-hallucination (Verified/Inferred/Unknown) + conflict policy | ✅ | §7,12,16,18,32 |
| Agentic skill without artificial complexity | ✅ | §7–9 |
| **v1.1** Governed multi-source discovery, no single-provider reliance | ✅ | §29,30 |
| **v1.1** Source Registry + controlled expansion (human-approved) | ✅ | §29 |
| **v1.1** Official source = final authority; provenance preserved | ✅ | §32 |
| **v1.1** Measurable coverage (checked/failed/gaps), no completeness claim | ✅ | §30.4 |
| **v1.1** Failure/retry: failed fetch ≠ "none found" | ✅ | §33 |
| **v1.1** Data-quality pipeline (classify + dedup added) | ✅ | §31 |
| **v1.1** Structured per-intent response schemas | ✅ | §34 |
| **v1.1** Monitoring designed, deferred to later phase | ✅ | §35, §22-P5 |
| **v1.1** No new agent added; discovery stays one agent | ✅ | §7.3, §9 |
| **v1.3** Matching upgraded to guardrailed agent; hard constraints rule-locked; agent count now 5 | ✅ | §7.5, §9, §18 |

---

## 29. Source Registry & Governance *(NEW — v1.1)*

**Why:** a general LLM/search can't be trusted to know or authorize every scholarship source. Coverage and trust must be **human-governed**. **Where:** `services/source_registry` + `sources/` + Postgres `source_registry` / `candidate_sources`. **Phase:** MVP-minimal in Phase 2; scales later. **Classification:** Service + DB (not an agent).

**Governed split:**
- **Human defines:** trusted sources, categories, boundaries, per-source rules, verification rules, extraction requirements, update frequency, reliability level.
- **AI does at runtime:** retrieve, extract, filter, normalize, classify, dedup, verify, store, match, reason — **only over approved sources**. It never self-authorizes a domain as authoritative.

**Registry entry (schema in §14 `source_registry`):** id, name, organization, country, region, type (gov / national-education / university / department / provider / foundation / NGO / embassy / international-org / research / API / approved-aggregator), official_status, domain, access_method (api/mcp/web), discovery_role, verification_role, reliability_level, update_frequency, status, extraction_rules, constraints, last_checked_at, last_success_at, notes.

**Controlled expansion (candidate → approved):**
```
Approved Registry ──► Runtime Discovery ──► {Approved source: use per rules}
                                        └──► {Candidate source (pending)} ──► source_validate workflow
                                                     reachability + type + reliability checks
                                                              ▼ [HUMAN APPROVAL GATE]
                                                        promote to Approved
```
A newly discovered source is **never** authoritative until approved. MVP = manual approval; Phase 5 = semi-automated checks, still human-confirmed.

---

## 30. Multi-Source Discovery & Coverage Engine *(NEW — v1.1)*

**Why:** maximize *practical* coverage without single-provider reliance. **Where:** Discovery Agent (§7.3) + `sources/` connectors + `coverage` service. **Phase:** Phase 2 (seed), Phase 5 (analytics). **Classification:** Agent (orchestration) + Tools (connectors) + Service (coverage).

**30.1 Channels (combined per registry):** approved scholarship APIs · MCP-connected services · approved search tools · official gov/national sources · university (+ department) pages · provider/foundation sites · research/fellowship sources · carefully selected trusted aggregators (signal only).

**30.2 Rule — no per-source agents:** each channel is a **connector/tool**; combining/dedup/verify are **services/workflows**. One Discovery Agent orchestrates.

**30.3 Exhaustive (systematic) search strategy:** the agent builds a **query plan** across dimensions — country · university · degree level · field · nationality · funding type · intake · academic/language requirements · scholarship type (gov/university/provider) · research/fellowship · current & upcoming deadlines — rather than one generic query like *"scholarships for Pakistani students."* Multiple targeted strategies run through approved tools.

**30.4 Coverage engine (measurable, not guaranteed):** derived from `source_registry` + `source_fetch_log`, exposes per country/region: configured sources · active sources · successfully checked · failed · last checked · **countries covered · universities covered · providers covered** · newly discovered candidates · verified opportunities · **coverage gaps**. Coverage spans both high-visibility destinations and **less-obvious countries/regions** (e.g., China, Norway, Morocco, other European/African/Asian/Middle-Eastern/Americas/Oceania sources). The UI states coverage as *measured*, and never claims "no scholarship missed" or 100% global coverage.

```
Registry (per country/type) ─► Discovery Agent ─► [API | official | search] connectors
        │                                                   │
        └─────────────── coverage service ◄── source_fetch_log (ok/fail/last_checked)
                                   ▼
                 Coverage summary: checked / failed / gaps  ─► UI
```

---

## 31. Data-Quality Pipeline *(EXPANDED — v1.1)*

**Why:** raw candidates from many sources need controlled, provenance-preserving processing. **Where:** `ingestion` workflow (§10) + supporting services. **Phase:** Phase 2. **Classification:** Workflow + Services (+ extraction/classify tools).

```
DISCOVER → EXTRACT → NORMALIZE → CLASSIFY → DEDUPLICATE → VERIFY(official) → STORE → INDEX → MATCH
   provenance (source_id, url, fetched_at) carried at every step
```
- **Classify (new):** degree level, funding type, provider type, country, field — rules + LLM tool for fuzzy.
- **Deduplicate (new):** deterministic keys (name+university+intake) + vector similarity threshold for near-dupes.
- **Validation rules:** required fields (name, degree_level, country, at least one official source, deadline-or-status) must be present/typed before `STORE`; missing required fields → held as incomplete, flagged, not published as fact.

---

## 32. Verification, Evidence & Conflict Resolution *(NEW — v1.1)*

**Why:** third-party data is a signal; the **official source is the final authority**. **Where:** `verification` + `conflict_resolution` services, reusing the Research/Q&A agent's grounding decision. **Phase:** Phase 2. **Classification:** Service (+ agent grounding).

- **Provenance on every record:** `scholarship_sources` stores source_id, url, retrieved_at, verified_at, verification_status (verified / unverified / conflicting), reliability, evidence_snippet.
- **Lifecycle/freshness status (v1.2, first-class):** each scholarship carries `lifecycle_status` ∈ `newly_discovered · verified · unverified · updated · expired · closed · reopened · stale · source_unavailable`, plus `retrieved_at` / `last_verified_at` / `deadline`. Freshness is a first-class field, not an afterthought.
- **Rule:** the system never presents unsupported claims as fact. If official info can't be confirmed → mark **unverified / could-not-confirm**, don't invent or guess. It does not invent facts, eligibility, spouse/dependent policy, funding, or deadlines.
- **Conflict-resolution policy (when sources disagree):** prefer **official over third-party**, **more-recent over older**, **higher-reliability over lower**; if still unresolved → mark **conflicting** and surface both with sources rather than picking silently.

---

## 33. Failure & Retry Strategy *(NEW — v1.1)*

**Why:** a failed fetch must never masquerade as "no scholarship found." **Where:** `sources/` retry policy + `source_fetch_log`. **Phase:** Phase 2 (basic), Phase 5 (health monitoring). **Classification:** Service / cross-cutting.

| Failure | Handling |
|---|---|
| Source unavailable / timeout | log `fail/timeout`, retry per policy (backoff, capped), mark source `failing` after N |
| API / MCP tool error | log, retry; if persistent, flag source health, surface as coverage gap |
| Page structure changed / extraction fails | log, keep prior record, flag for re-check; never overwrite good data with empty |
| Verification fails | store as `unverified`, do not publish as fact |
| Scholarship can't be confirmed | present as *could-not-confirm*, not absent |

Every outcome lands in `source_fetch_log`, feeding coverage (§30.4) and source health (Phase 5).

---

## 34. Structured Response Schemas *(NEW — v1.1)*

**Why:** controlled input + controlled sources demand **controlled output** — the agent must not answer free-form on scholarship facts. **Where:** `schemas/` used by agents/services. **Phase:** Phase 1 (Q&A/match), Phase 2 (discovery). **Classification:** Service + Det. code (per-intent contracts).

Principle: **CONTROLLED INPUT → CONTROLLED TOOLS/SOURCES → CONTROLLED PROCESSING → STRUCTURED OUTPUT.** Examples:

| Intent | Response schema |
|---|---|
| Eligibility? | verdict · eligibility criteria · failed criteria · missing information · conditions · evidence · official source · last_verified |
| Spouse/dependents? | verdict (Yes/No/Unclear) · policy · conditions · evidence · official source · last_verified |
| Funding/benefits? | tuition · stipend · accommodation · travel · insurance · other benefits · conditions · evidence/source · last_verified |
| Is this a match? | match strength (Strong/Possible/Not) · hard-constraint results · soft-preference results · exclusions triggered · missing info · reasons · evidence |
| How do I apply? | steps · required docs · missing docs · deadlines · official application route · next action |

Each schema is **intent-specific** — no single universal template — while every answer stays predictable, evidence-backed, and controlled. Every schema carries **evidence + official source + verification/last_verified label** so uncertainty is explicit, never hidden.

---

## 35. Continuous Monitoring *(DESIGNED NOW, BUILT LATER — v1.1)*

**Why:** approved sources change; the platform should catch new/closed/changed scholarships, not only search on demand. **Where:** `source_monitor` workflow + scheduler + notification service. **Phase:** **Phase 5** (not MVP — avoid over-engineering early). **Classification:** Workflow + scheduler (deterministic; no agent).

```
Source Registry ─► Scheduled Monitoring ─► New/Changed content ─► ingestion (§31) ─► profile re-match ─► Notify/Alert
```
Detects: newly published scholarships · changed requirements/deadlines · closing/reopening · funding changes · source failures · stale records. MVP ships the registry + fetch-log that make this a later drop-in — **no MVP monitoring load.**

---

## Revision Notes — v1.0 → v1.1

| # | Change | Modified/New | Where | MVP or Future | Classification |
|---|---|---|---|---|---|
| 1 | Controlled-coverage principle | Modified | §1, §2 | MVP | principle |
| 2 | Registry-driven, multi-source discovery flow | Modified | §3.1, §4 | MVP (seed) | Agent+Tools |
| 3 | New backend modules (`sources/`, `schemas/`, services) | Modified | §6 | MVP | Service/Tool |
| 4 | Discovery Agent refined (registry-driven, one agent) | Modified | §7.3 | MVP | Agent |
| 5 | Decision-matrix rows for all new capabilities | Modified | §9 | — | mixed |
| 6 | New workflows: `ingestion`, `source_validate`, `source_monitor` | Modified | §10 | MVP (2), Future (monitor) | Workflow |
| 7 | Connectors + classify tool | Modified | §11 | MVP | Tool |
| 8 | Qdrant ≠ source of truth for fresh facts | Modified | §12 | MVP | rule |
| 9 | Registry/log/candidate/provenance tables | Modified | §13, §14 | MVP | DB |
| 10 | MCP registry-governed, multi-connector | Modified | §15 | MVP | Tool |
| 11 | Phase 2 expanded; Phase 5/6 monitoring+coverage+expansion | Modified | §22 | MVP + Future | phases |
| 12 | Phase-2 example issues (registry, ingestion) | Modified | §24 | MVP | spec |
| 13 | Testing rows for governance/ingestion/coverage/etc. | Modified | §25 | — | tests |
| 14 | Assumptions A9–A12 | Modified | §27 | — | — |
| 15 | Source Registry & Governance | **New** | §29 | MVP-minimal | Service+DB |
| 16 | Multi-Source Discovery & Coverage Engine | **New** | §30 | MVP (seed), Future (analytics) | Agent+Tool+Service |
| 17 | Data-Quality Pipeline (classify+dedup) | **New/Expanded** | §31 | MVP | Workflow+Service |
| 18 | Verification, Evidence & Conflict Resolution | **New** | §32 | MVP | Service |
| 19 | Failure & Retry Strategy | **New** | §33 | MVP (basic) | Service |
| 20 | Structured Response Schemas | **New** | §34 | MVP | Service |
| 21 | Continuous Monitoring | **New (design)** | §35 | Future (Phase 5) | Workflow+scheduler |

**Net effect:** discovery moved from "autonomous web search" to a **governed, multi-source, verified, measurable** system — with **zero new agents** and no new heavyweight infrastructure. Every addition is a service, workflow, tool/connector, DB table, or schema, each justified in §9.

---

## 36. Capability Scope Labels — MVP / Post-MVP / Production / Future *(NEW — v1.2)*

Every major capability, explicitly labeled. **MVP** = built first for personal use; **Post-MVP** = next practical increment; **Production** = hardening/scale; **Future/Advanced** = SaaS or advanced automation.

| Capability | Label |
|---|---|
| Auth + user profile | **MVP** |
| Advanced constraint-typed profile (hard/soft/exclusion/missing) | **MVP** |
| Controlled Source Registry + initial high-quality source set | **MVP** |
| MCP/API integrations (justified) | **MVP** |
| Multi-source discovery + normalization + deduplication | **MVP** |
| Official-source verification + provenance | **MVP** |
| Scholarship DB + Qdrant retrieval (justified) | **MVP** |
| Profile matching (hard/soft/exclusion) | **MVP** |
| Scholarship URL matching | **MVP** |
| Scholarship Q&A + structured responses | **MVP** |
| Basic application planning + document requirement tracking | **MVP** |
| Basic CV/SOP assistance | **MVP** |
| Coverage summary (basic counts + gaps) | **MVP** |
| Application/Submission Agent orchestration (prepare + approvals, no live send) | **Post-MVP (Phase 4)** |
| Full document intelligence (inconsistency detection at depth) | **Post-MVP** |
| Scheduled source monitoring + notifications | **Production (Phase 5)** |
| Coverage analytics dashboards + source-health | **Production (Phase 5)** |
| Semi-automated candidate-source validation | **Production (Phase 5)** |
| Large-scale source/country expansion | **Future/Advanced (Phase 6)** |
| Live approval-gated portal submission | **Future/Advanced (Phase 6)** |
| SaaS multi-tenancy, billing, quotas | **Future/Advanced (Phase 6)** |

**MVP rule:** do **not** force full autonomous application submission or production monitoring into the MVP.

---

## 37. Future Submission Architecture *(NEW — v1.2, high-level, deferred)*

Actual submission is **Future (Phase 6)**, but its shape is fixed now so nothing built earlier blocks it.

```
Identify portal → portal-specific workflow → (browser interaction where needed) →
form-field mapping ← user-data mapping → document upload → validation →
completeness checks → FINAL REVIEW → [EXPLICIT HUMAN APPROVAL] → submit → confirmation → tracker
```
- **Classification:** the Application/Submission Agent (existing, §7.4) orchestrates; portal field-mapping is a **tool**; an optional per-portal form-filling **sub-agent** is added *only* if portals prove too heterogeneous for a tool (§8) — never by default.
- **CRITICAL SAFETY RULE:** the system **never autonomously performs the final irreversible submission.** A mandatory human review/approval step immediately precedes submit. This rule is invariant across MVP, Production, and SaaS.

---

## 38. v1.2 Revision Notes + Consistency Audit

### What changed (v1.1 → v1.2)
| # | Change | Modified/New | Where | Label |
|---|---|---|---|---|
| 1 | Per-capability I/O contracts for all buttons | **New** | §5.1 | MVP |
| 2 | Comprehensive scholarship schema + per-field value-status | Modified | §14 | MVP |
| 3 | Constraint-typed living profile (hard/soft/exclusion/missing) | Modified | §14 | MVP |
| 4 | No-guess value-status discipline | Modified | §14 | MVP |
| 5 | Document intelligence strengthened | Modified | §16 | MVP/Post-MVP |
| 6 | CV/SOP requirement-awareness (Europass, specified questions) | Modified | §16 | MVP |
| 7 | Application material model + long-term workflow | Modified | §17 | MVP→Future |
| 8 | Matching uses hard/soft/exclusion differently; Strong/Possible/Not | Modified | §18 | MVP |
| 9 | Coverage metrics (countries/universities/providers) + regions | Modified | §30.4 | MVP/Prod |
| 10 | Lifecycle/freshness status first-class | Modified | §32, §14 | MVP |
| 11 | Response schemas expanded (eligibility/funding/matching) | Modified | §34 | MVP |
| 12 | Capability Scope Labels | **New** | §36 | — |
| 13 | Future Submission Architecture | **New** | §37 | Future |

### Consistency audit (§14 checks — no contradictions)
- **Agent count (v1.3):** **5 agents** — Main, Research/Q&A, Discovery, Application/Submission, Matching. The Matching Agent is guardrailed (hard constraints rule-locked). Every place that previously said "4 agents" is updated (§1, §4, §6, §7, §21). ✅ (§7, §9)
- **Source strategy ↔ MCP ↔ discovery:** registry governs connectors; discovery reads registry; MCP is registry-bound. Consistent. ✅ (§15, §29, §30)
- **RAG/Qdrant ↔ verification:** Qdrant is knowledge-retrieval only; fresh facts verified vs official source; freshness status first-class. No conflict. ✅ (§12, §32)
- **PostgreSQL ↔ schema:** value-status/lifecycle/constraint tables all in Postgres; provenance intact. ✅ (§13, §14)
- **Profile ↔ matching:** `profile_criteria` kinds drive the matching logic; hard constraints computed deterministically pre-agent and locked; hard-fail ≠ soft-miss. ✅ (§14, §18, §7.5)
- **UI ↔ backend:** §5.1 contracts map 1:1 to agents/workflows/services already defined; OpenAPI generated from them. ✅
- **Application workflow ↔ submission safety:** prepare/approve in Phase 4; live submit Phase 6 with mandatory human approval. ✅ (§17, §37)
- **MVP ↔ future scope:** §36 labels every capability; submission/monitoring/expansion/SaaS all Future. ✅ (§2, §22, §36)
- **Spec-driven ↔ phase independence:** each phase executable from blueprint + phase spec + contracts + issues; no chat context. ✅ (§22, §23)

### What changed (v1.2 → v1.3) — Matching becomes a guardrailed agent
| # | Change | Modified/New | Where | Label |
|---|---|---|---|---|
| 1 | Version → v1.3 + change-theme line | Modified | header | — |
| 2 | "Exactly 4 agents" → "Exactly 5 agents (…Matching)"; Matching removed from not-an-agent list | Modified | §1 | — |
| 3 | Agent list in system diagram + walkthrough (add Matching; drop matching from services) | Modified | §4 | — |
| 4 | Backend modules: matching moved agents/; add `hard_constraints` service | Modified | §6 | — |
| 5 | Intro "Four agents" → "Five agents"; diagram note | Modified | §7 | — |
| 6 | **New §7.5 Matching Agent (guardrailed)** with guardrails a–f + diagram | **New** | §7.5 | MVP |
| 7 | Decision Matrix: Matching row → Agent, footnote ¹ | Modified | §9 | — |
| 8 | Closing line now "Matching Agent (guardrailed) — see §7.5" | Modified | §18 | MVP |
| 9 | SDK row → 5 agents + output-guardrails note | Modified | §21 | — |
| 10 | Phase 1 scope → guardrailed Matching Agent; tests → guardrail-violation build-breakers | Modified | §22 | MVP |
| 11 | Testing: Matching row + guardrail enforcement | Modified | §25 | Phase 1 |
| 12 | Assumption A13 | Modified | §27 | — |
| 13 | Coverage audit + consistency audit: agent count now 5, no contradiction | Modified | §28, §38 | — |

**Net effect (v1.3):** Matching moves from a pure service to a **guardrailed agent** — better judgment on soft/fuzzy criteria, while hard-constraint facts stay deterministic and rule-locked. Agent count 4 → 5. No other agent, workflow, service, schema, or phase changes.

---

*End of Master Blueprint v1.3 — Matching is now a guardrailed agent; hard constraints remain rule-locked; ready for phase-by-phase execution from artifacts alone.*

---

## 39. Development Execution Model — Manager / Worker *(NEW — v1.4)*

The project is built through **two separate Claude Code contexts with no shared memory**; the developer manually transfers information between them.

- **Manager = Claude Code Chat UI** — planning, reasoning, decomposition; produces focused implementation prompts and reads completion summaries. Holds the Master Blueprint.
- **Worker = Claude Code CLI** — implementation; inspects repo, writes/edits files, runs commands + tests, validates. Receives one Workstream Brief at a time.

```
                       ┌───────────────── Manager Handoff Packet (Manager sessionⁿ → sessionⁿ⁺¹) ───────────────┐
                       ▼                                                                                          │
Manager (Chat UI) ──produces──► Workstream Brief ──[you paste]──► Worker (CLI)                                    │
       ▲                                                              │ implements + tests                       │
       │                                                              ▼                                          │
       └──────────[you paste]◄── Completion Summary ◄──────────── Worker (CLI)                                   │
       │                                                                                                          │
       └── at end of Manager session: distill Completion Summary + state → Manager Handoff Packet ───────────────┘
```

**Two handoff axes, three artifacts:**
- **Manager → Worker:** `Workstream Brief` (§42) — focused execution context.
- **Worker → Manager:** `Completion Summary` (§42) — verified implementation result.
- **Manager → new Manager session:** `Manager Handoff Packet` (§42) — compact planning state so a fresh Chat UI session continues without the old conversation.

**Cycle:** plan → Brief → (transfer) → implement + test → Completion Summary → (transfer) → update plan → *(when starting a fresh Manager session)* boot from Manager Handoff Packet → plan next.

**Design consequences (these shape §40–§42):**
- The two contexts do **not** share history — every handoff must be **self-contained** and explicit.
- **Technical dependencies are fine** (Matching needs Profile data); **conversational dependencies are not** ("remember what we discussed"). Continuity lives in **artifacts** (specs, contracts, code, summaries), never in chat history.
- A **fresh session should be startable at any completed Workstream boundary** — but not after every tiny task.

---

## 40. Workstream Decomposition *(NEW — v1.4)*

A **Workstream** sits between Phase and Task: **a meaningful, coherent, independently executable unit of several related tasks** whose completion is a natural fresh-session boundary.

```
Project → Phases → Workstreams → Tasks → Issues
                    └── the fresh-session boundary (not Task, not Phase)
```

**What makes a good Workstream (heuristics, not rules):**
- Delivers one coherent capability slice that can be **built + tested + left stable** in one working context.
- Ends with **stable contracts** (schema/OpenAPI/interfaces/tests) the next Workstream can depend on **without chat history**.
- Small enough to fit comfortably in one CLI session's context; large enough to avoid session-churn overhead.
- Respects **technical** dependencies (ordered by them), not conversational ones.
- **Do not** make every task its own session; **do not** optimize for the smallest unit. Optimize for *practical, meaningful* units.

**Example decomposition (illustrative — the Manager finalizes per phase):**

| Phase | Example Workstreams | Ends with (contracts) |
|---|---|---|
| 0 Foundation | WS0.1 repo+compose+CI · WS0.2 auth+base schema+OpenAPI v0 | OpenAPI v0, schema v0, ADRs |
| 1 Core tool | WS1.1 Profile (constraint-typed) · WS1.2 URL-match workflow · WS1.3 RAG + Research/Q&A agent · WS1.4 `hard_constraints` service + Matching Agent (guardrailed) | Profile/Requirement/Match models frozen; RAG collection; endpoints |
| 2 Discovery | WS2.1 Source Registry + governance · WS2.2 connectors · WS2.3 ingestion/data-quality · WS2.4 coverage + ranking | registry/log/candidate/provenance schemas; connector interface |
| 3 Docs/Gen | WS3.1 doc_pipeline · WS3.2 cv_gen · WS3.3 sop_gen | material→requirement mapping; generated-doc contracts |
| 4 Application | WS4.1 app_plan + tracker · WS4.2 Application/Submission Agent + approval gates | case model, progress/approval contract |
| 5 Production | WS5.1 monitoring · WS5.2 coverage analytics + source health | scheduler + alert contracts |
| 6 SaaS | WS6.1 multi-tenancy · WS6.2 billing/quotas · WS6.3 live submission | tenancy + submission contracts |

Each Workstream maps to a phase spec section and its own set of GitHub issues (§24).

---

## 41. Context Ladder — what each session actually needs *(NEW — v1.4)*

The Master Blueprint must **not** be pasted into every session. Information is tiered; a session loads only its tier(s).

| Tier | Artifact | Size | When loaded |
|---|---|---|---|
| T0 | **Project Charter** — 1-page: vision, 5-agent map, tech boundaries, hard rules (anti-hallucination, HITL submission, source governance) | tiny, stable | every session (cheap) |
| T1 | **Phase Spec** — objective, scope, contracts for the current phase | small | when working in that phase |
| T2 | **Workstream Brief** — the actual session input (§42) | small, self-contained | the working session |
| T3 | **Contracts** — OpenAPI, JSON schemas, DB schema, ADRs (from repo) | as needed | referenced, not pasted |
| — | **Master Blueprint** | large | Manager (Chat UI) only; rarely the Worker |

**Rule:** the **Worker (CLI)** normally receives **T0 + T2 + pointers to T3 in the repo** — *not* the full blueprint. A **fresh Manager (Chat UI) session** boots from **T0 + the latest Manager Handoff Packet (§42) + T3 contracts as needed** — *not* the full prior conversation, and only reloading the full blueprint when a broad re-plan is required. The Manager holds/distills the blueprint; the Worker never needs it.

`specs/` layout supports this:
```
specs/
  charter.md            # T0 — regenerated only when a hard rule changes
  phase-N/spec.md       # T1
  phase-N/workstreams/WSN.x.md   # T2 briefs
  contracts/            # T3 (OpenAPI, schemas, ADRs)
  handoffs/WSN.x.summary.md       # Worker → Manager completion summaries (§42)
  handoffs/manager-state.md       # Manager → new-Manager rolling handoff packet (§42)
```

---

## 42. Handoff Artifacts — three distinct artifacts *(NEW — v1.4, expanded v1.5)*

Because no two sessions share memory, these artifacts *are* the continuity mechanism. There are **three**, each with a different author, consumer, and purpose — **do not merge them**:

| Artifact | Author | Consumer | Purpose | Focus |
|---|---|---|---|---|
| **Workstream Brief** | Manager | Worker (CLI) | tell the worker exactly what to implement now | focused **execution** |
| **Completion Summary** | Worker (CLI) | Manager | report the verified result of a Workstream | **implementation / verification** |
| **Manager Handoff Packet** | Manager (session ending) | **new Manager session** | carry compact planning state so a fresh Chat UI session plans the next WS | cumulative **planning** |

Keep all three to **minimum sufficient context** — never a transcript.

**Workstream Brief (Manager → Worker):**
```
Workstream: WS<id> — <name>              Phase: <n>
Goal: <one sentence — the capability slice>
In scope: <bullet tasks>       Out of scope: <explicit exclusions>
Technical dependencies: <prior contracts/modules it builds on — names, not history>
Relevant contracts: <paths in specs/contracts + repo files to read first>
Hard rules that apply: <e.g., matching guardrails §7.5; no fabrication; provenance>
Implementation notes: <only what's non-obvious>
Definition of Done: <checklist>
Tests required: <list, incl. any build-breaking guardrail tests>
Deliverables: <files/endpoints/migrations expected>
```

**Completion Summary (Worker → Manager):**
```
Workstream: WS<id> — <name>              Status: done / partial / blocked
Completed: <what now works>
Changed: <files/modules created or modified — paths>
Key decisions: <choices a planner must know; deviations from the Brief>
Contracts produced/changed: <schemas/OpenAPI/interfaces — the stable surface next WS depends on>
Tests: <run + results; guardrail/regression status>
Known limitations / TODO: <carry-forward items>
Unresolved / blockers: <if any>
Next-session inputs: <what the next Brief will need — pointers, not narration>
```

### 42.1 Manager Handoff Packet (Manager session → new Manager session) *(v1.5)*

Produced by the Manager **at the end of a Chat UI session** (or when moving from WS-N to WS-N+1), by distilling: the prior Manager Handoff Packet + the Completion Summary/Summaries just received + the current plan. A **fresh Chat UI Manager session boots from this packet alone** (plus T0 Charter and repo contracts) — **never** from the old conversation. Recommended as a **rolling document** (`specs/handoffs/manager-state.md`, latest supersedes), with per-WS Completion Summaries archived beside it.

```
=== MANAGER HANDOFF PACKET ===
As of: WS<id> complete → planning WS<next-id>        Phase: <n>

PROJECT STATE
- One-line status + position in phase/workstream map (§40)

COMPLETED SO FAR (cumulative — do NOT rebuild these)
- <capability / module> — stable, contracts frozen
- <capability / module> — ...

JUST FINISHED (WS<id>)
- Key outcomes (distilled from its Completion Summary)
- Tests/status: <pass/fail, guardrail status>

IMPORTANT DECISIONS (still in force)
- <decision + 1-line why>  (link ADR if any)

ARTIFACTS & CONTRACTS AVAILABLE
- Created/changed files: <paths>
- Stable contracts next WS can rely on: <schemas / OpenAPI / interfaces — paths>

KNOWN LIMITATIONS / BLOCKERS / CARRY-FORWARDS
- <item> ...

NEXT WORKSTREAM: WS<next-id> — <name>
- Objective: <one sentence>
- Scope: <in / out>
- Technical dependencies it consumes: <from COMPLETED/CONTRACTS above>
- Context it needs (pointers only): <spec/contract/repo paths>
- Already-done guard: <things that exist so the new session won't recreate them>

NEXT ACTIONS (for the new Manager session)
- <first planning step> → produce WS<next-id> Workstream Brief (§42)

NOT INCLUDED (by design): full prior conversation / transcripts.
=== END PACKET ===
```

**Principle:** the Manager Handoff Packet answers *"what's true now + what to plan next"* for a **planner**; the Completion Summary answers *"what did the worker just verify"* for the **manager to absorb**. The packet **absorbs** the latest Completion Summary; it does not replace or duplicate the Brief. Minimum sufficient context, never maximum.

---

## 43. v1.4 Revision Notes

| # | Change | Modified/New | Where |
|---|---|---|---|
| 1 | Version → v1.4 + change theme; build method now "phase- and session-independent" | Modified | header |
| 2 | Phase plan cross-refs Workstreams | Modified | §22 |
| 3 | Development Execution Model (Manager/Worker, manual transfer, cycle) | **New** | §39 |
| 4 | Workstream Decomposition (layer + heuristics + example phase→WS map) | **New** | §40 |
| 5 | Context Ladder (T0–T3; Worker gets Charter+Brief, not full blueprint) + `specs/` layout | **New** | §41 |
| 6 | Handoff artifacts (Workstream Brief + Completion Summary templates) | **New** | §42 |

**Net effect (v1.4):** the project is now decomposable into **practical, meaningful Workstreams** that hand off between sessions via **explicit artifacts** (Charter, Brief, Summary, contracts) instead of conversation history — solving the token/context growth problem while preserving legitimate technical dependencies. **No architecture, agent, workflow, service, schema, or phase changed** — this is purely the execution/continuity model. Agent count remains **5**.

*Consistency note:* this reinforces the existing phase-independence (§22) and Spec-Driven (§23) principles; no contradiction introduced. "Session independence" is a **process** property (artifacts over chat history) — it does **not** mean every component becomes an agent (§14 principle upheld).

---

## 44. v1.5 Revision Notes

| # | Change | Modified/New | Where |
|---|---|---|---|
| 1 | Version → v1.5 + change theme | Modified | header |
| 2 | Execution cycle diagram now shows the Manager → new-Manager-session loop; names the two axes / three artifacts | Modified | §39 |
| 3 | Context ladder: explicit **Manager-session boot inputs** (Charter + Manager Handoff Packet + contracts), distinct from Worker boot inputs; `specs/handoffs/manager-state.md` added | Modified | §41 |
| 4 | §42 restructured to **three distinct artifacts** with an author/consumer/purpose table | Modified | §42 |
| 5 | **New §42.1 Manager Handoff Packet** template (Manager → new Manager session) | **New** | §42.1 |

**Gap closed:** previously the blueprint defined Manager→Worker (Brief) and Worker→Manager (Completion Summary) but **not** Manager-session → new-Manager-session continuity. v1.5 adds the **Manager Handoff Packet** as a *distinct* artifact — planning-focused, cumulative, "already-done" guarded — so a fresh Chat UI Manager session continues from a compact packet, not the old conversation. The three artifacts stay separate: Worker gets focused execution context (Brief), the Manager absorbs verified results (Completion Summary), and a new Manager session inherits planning state (Handoff Packet).

**Net effect (v1.5):** the continuity model is now complete across **all three transfer axes**. No architecture, agent, workflow, service, schema, or phase changed. Agent count remains **5**.

---

*End of Master Blueprint v1.5 — architecture stable (5 agents); full three-axis session-continuity model (Brief · Completion Summary · Manager Handoff Packet); ready for Workstream-by-Workstream execution from artifacts alone.*

---

## 45. v1.6 Revision Notes — production-grade concepts integration

| # | Change | Modified/New | Section | MVP/Phase | Why (one line) |
|---|---|---|---|---|---|
| 1 | v1.6 change theme + FDE positioning pointer | Modified | header | — | Records the integration; routes FDE material out of the technical doc |
| 2 | **Agent Loop & Human Gates** named + cross-referenced | Consolidation | §7.0 | MVP | Loop Engineering already existed implicitly; now named, no new loops/gates |
| 3 | **Harness** (model + permission walls + non-LLM checks) named + **sandbox requirement** added | Consolidation + 1 new req | §9.1 | MVP (sandbox mechanism ⚠️ TBD) | Frames model+harness; adds the one missing piece (isolated safe execution) |
| 4 | **Evals & the Checker** (scored grading, checker-graded, CI score-gate) | **New** | §25.1 | MVP, established Phase 0/1 | Distinct from pass/fail tests; gates change on score regression |
| 5 | Eval harness scaffold + CI score-gate | Modified | §22 Phase 0 | Phase 0 | Establish evals early, not deferred |
| 6 | First real evals attached | Modified | §22 Phase 1 | Phase 1 | Matching-correctness + Q&A-groundedness + checker-accuracy |
| 7 | **Minimal cloud runtime** (public endpoint + managed services) | Modified | §22 Phase 2, §21 | end of Phase 2 | "Leaving the laptop" early; full prod/SaaS infra stays Phase 5/6 |
| 8 | FDE (Forward Deployed Engineer) material excluded → positioning README | Pointer only | header note | — (business) | Positioning, not architecture |

### Consistency audit (v1.6)
- **No new agent added** — Loop/Harness/Evals/Deploy are control/verification/infra concerns; §9 decision-matrix discipline intact. ✅
- **Loop (§7.0) & Harness (§9.1) are consolidations** — they cross-reference existing sections, don't duplicate them. ✅
- **Evals (§25.1) are distinct from Testing (§25)** — scored + checker-graded + score-gate; timing moved early in §22 (Phase 0/1). ✅
- **Cloud runtime added early (end Phase 2)** without deleting Phase 5 (production) or Phase 6 (SaaS) infra. ✅
- **FDE (Forward Deployed Engineer) material excluded** from technical sections; routed to a separate positioning README via a one-line pointer. ✅
- **All existing sections, agent names (5), phase numbers (0–6), requirement IDs preserved**; nothing deleted or reordered. ✅
- **Nothing invented** — undecided items are marked ⚠️ NOT YET DEFINED — needs decision. ✅

### ⚠️ NOT YET DEFINED — needs decision (introduced/confirmed by v1.6)
1. **Sandbox isolation mechanism** for safe tool/browser-portal execution (§9.1).
2. **Eval thresholds/targets** — minimum scores, regression tolerance, checker-accuracy bar (§25.1).
3. **Cloud provider + managed services** for the minimal cloud runtime (§22 Phase 2, §21).
4. **FDE (Forward Deployed Engineer) positioning README** — separate doc, not yet written (header note).

**Net effect (v1.6):** the blueprint now explicitly names its **loop** and **harness** discipline, adds a **scored eval + CI gate** established early, and **leaves the laptop** at end of Phase 2 — all with **zero new agents** and no deleted content. Business/positioning stays out. Agent count remains **5**.

---

*End of Master Blueprint v1.6 — 5 agents; loop + harness named; evals & CI score-gate (early); minimal cloud runtime at Phase 2; FDE/positioning routed out. Ready for Workstream-by-Workstream execution from artifacts alone.*

<!-- The section below is a separate document type (PRD). It defines WHAT/WHY at the product and agent-behavior level. It does not replace or duplicate the architecture defined above. -->

# PART B — PRODUCT REQUIREMENTS DOCUMENT (PRD) — v1

| Field | Value |
|---|---|
| PRD version | v1 |
| Last updated | 2026-09-05 |
| Status | draft |
| Scope | Agent behavior contracts for the 5 agents defined in the Blueprint (§7) |

**Relationship note:** This PRD governs **agent behavior**. The Blueprint above governs **system architecture**. **Blueprint > PRD** in the truth hierarchy for structural decisions; **PRD > Blueprint** for agent-decision-boundary questions. Same agent names, same section numbers (§) as the Blueprint — cross-reference freely.

**How to read each entry:** *Judgment Zone* = where the LLM may reason. *Hard-Gated Zone* = facts/decisions the LLM may NEVER decide; enforced by deterministic non-LLM code, and **LLM output is ignored/overridden if a gate fails**.

---

## B1. Main Orchestrator Agent  *(Blueprint §7.1)*

- **Purpose:** Interpret the user's natural-language request and route it to exactly one capability.
- **Inputs:** User message + user/profile context (current user id, conversation context).
- **Judgment Zone:** Intent understanding; extraction of compound constraints (e.g., "fully funded IT Master's, GRE not required"); which capability/agent/workflow to dispatch to; composing the final reply.
- **Hard-Gated Zone:**
  - Must **not** perform capability work itself (matching, verification, generation) — must delegate. LLM "answers" that bypass the owning component are ignored.
  - Scholarship facts in the final reply must be **schema-structured** (§34) and carry evidence/source — free-text factual claims are not permitted.
- **Outputs:** A dispatched call to one agent/workflow/service; then a composed, schema-structured response back to the user.
- **Failure Behavior:** ⚠️ NOT YET DEFINED IN BLUEPRINT — needs decision (routing-failure policy). General async job pattern (submit→poll/stream, §5 rules) applies to long calls; clarification-on-ambiguous-intent is implied but unspecified.
- **Success Criteria:** Correct dispatch for compound requests; final replies use structured schemas (§34). Tied to Agent tests — routing (§25).

---

## B2. Research / Q&A Agent  *(Blueprint §7.2)*

- **Purpose:** Answer scholarship questions **grounded** in reliable sources, and verify important claims during discovery.
- **Inputs:** Question + scholarship/source context; RAG index; approved connectors.
- **Judgment Zone:** Whether an answer is already grounded in the knowledge base (RAG) vs. needs live official-source research; which approved source to consult; grounding-quality assessment (Self-RAG reflection).
- **Hard-Gated Zone:**
  - **No unsupported claim as fact** (§32). If the grounding-check fails, the LLM's draft answer is **overridden** → escalate to live official-source research; if still unverifiable → labeled **Unknown / could-not-confirm**, never guessed (§15/§32 no-guess).
  - **Official source is final authority**; third-party = signal only. Conflict resolution is a **deterministic policy** (official/current/higher-reliability preferred), not an LLM call (§32).
  - Every answer carries evidence + source + verification label.
- **Outputs:** Grounded answer + evidence + confidence label (Verified / Inferred / Unknown) → back to Main Orchestrator.
- **Failure Behavior:** Weak grounding → live fetch (§12). Source unavailable/timeout → logged to `source_fetch_log`, retried per policy, surfaced as coverage gap; **failed fetch ≠ "no scholarship"** (§33). Unconfirmable → return "could-not-confirm".
- **Success Criteria:** Answers cite a source; unsupported claims labeled Unknown and never fabricated; grounding test passes (§25 anti-hallucination, RAG/retrieval).

---

## B3. Discovery Agent  *(Blueprint §7.3)*

- **Purpose:** Find candidate scholarships matching the profile by orchestrating retrieval across **approved** sources (not the open web at large).
- **Inputs:** Profile + constraints + Source Registry view (approved sources/rules for the relevant countries/types).
- **Judgment Zone:** Which discovery strategies/connectors to run for this profile/country; multi-strategy query planning across dimensions (§30.3); candidate triage; iterating when a source fails or returns thin results.
- **Hard-Gated Zone:**
  - **No self-authorized sources.** A newly seen source becomes a **candidate (pending)** and is **never** treated as authoritative until human approval (§29). Connectors reject any fetch whose `source_id` is not `active` — LLM cannot override this.
  - **Failed fetch ≠ "none found"** — must be logged + retried, surfaced as a coverage gap (§33).
  - Coverage is reported as **measured** (checked/failed/gaps), never asserted as complete (§30.4).
- **Outputs:** Candidate pool (records + provenance) + candidate-source flags + coverage summary → deterministic data-quality pipeline (§31) → Matching.
- **Failure Behavior:** Source unavailable/API/MCP error/timeout → log to `source_fetch_log`, retry per policy (backoff, capped), mark source `failing` after N, surface as coverage gap (§33).
- **Success Criteria:** Profile-driven discovery across ≥2 source types returns ranked, verified, source-attributed results + coverage summary; a discovered source lands as *candidate* (not auto-authoritative). Tied to §22 Phase 2 DoD + §25 source-governance / connector-contract tests.

---

## B4. Application / Submission Agent  *(Blueprint §7.4)*

- **Purpose:** Drive a full application prepare → review → (future) submit, **step-by-step**, requesting missing details and requiring explicit approval at each gate.
- **Inputs:** Target application + user profile/documents + user approvals; the shared workflows it orchestrates (`app_plan`, `doc_pipeline`, `cv_gen`, `sop_gen`, readiness).
- **Judgment Zone:** The next needed step for *this* scholarship/portal; what's still missing; sequencing; which workflow to invoke next; what to request from the user.
- **Hard-Gated Zone:**
  - **Never submit live without explicit human approval** — the final irreversible submit is gated; LLM cannot self-approve (§17, §37). Live external send is deferred to Phase 6.
  - **No fabrication** in generated materials — `ground_check` overrides/rejects any content not traceable to real user data (§16).
  - Deterministic mode parity: the same steps exist as plain buttons; the agent adds no logic the buttons lack.
- **Outputs:** An assembled, approved application case + readiness assessment (labeled Complete / Missing / User-must-obtain / AI-can-generate / Needs-human-review / Needs-official-verification) → Application Tracker / user; (future) a submitted application.
- **Failure Behavior:** Missing item → request from user and pause; blocked → surface as blocked. ⚠️ NOT YET DEFINED IN BLUEPRINT — needs decision (retry/escalation policy for live-submission failures, a Phase 6 concern).
- **Success Criteria:** Agent walks steps, requests missing docs, obtains approvals, outputs a ready case; **button-vs-agent workflow parity** (same case object); **approval-gate enforcement** (nothing advances past a gate without approval). Tied to §22 Phase 4 tests.

---

## B5. Matching Agent (guardrailed)  *(Blueprint §7.5 / §18)*

- **Purpose:** Produce an explainable eligibility / match-strength verdict, reasoning over soft/fuzzy criteria while hard constraints stay rule-locked.
- **Inputs:** Profile + scholarship requirements + **pre-computed hard-constraint & exclusion results** (from the deterministic `hard_constraints` service), passed as **read-only** context.
- **Judgment Zone:** Soft preferences and fuzzy/unstructured criteria only — e.g., "relevant work experience," "research fit," weighing several soft preferences together.
- **Hard-Gated Zone:**
  - **Hard-constraint lock:** GPA threshold, degree level, nationality, deadline, mandatory tests are computed deterministically **before** the agent runs and are **read-only**. The agent cannot alter, override, or re-derive them — **LLM output is ignored/overridden if it contradicts a hard-constraint result.**
  - **Schema-locked output** (Pydantic-enforced §18 verdict object) — no free-text verdicts.
  - **Evidence-required** — every matched/failed/unmet criterion needs an evidence reference or it's rejected by an output guardrail.
  - **No-guess** — any field with `value_status = unknown` (§14) must surface as `missing_information`, never invented.
  - **Bounded tools** — only profile reader / requirement reader / fuzzy-criterion resolver; **no web/search/fetch**.
  - **Deterministic ranking** — the ranking formula stays in the `ranking` service; the agent supplies inputs but never reorders results.
- **Outputs:** The §18 verdict object (eligibility_verdict + match_strength + hard/soft/exclusion breakdown + missing_information + evidence) → deterministic `ranking` service → results.
- **Failure Behavior:** Output failing schema/evidence/no-guess guardrails is **rejected** (not returned). A hard-constraint guardrail violation is a **build-breaking failure**, not a warning (§25). ⚠️ NOT YET DEFINED IN BLUEPRINT — needs decision (runtime fallback when the agent repeatedly fails guardrails: reject-and-retry vs. deterministic-only fallback).
- **Success Criteria:** Passes the fixed **verdict-correctness matrix** (known profile × known scholarship → expected verdict) on every build; **guardrail enforcement test** (hard-constraint lock never overridden) passes. Tied to §22 Phase 1 + §25 Matching.

---

*End of Part B — PRD v1 (draft). Governs agent behavior; defers to the Blueprint for architecture. Items marked ⚠️ require a decision before the relevant phase implements them.*
