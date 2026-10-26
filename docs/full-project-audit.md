# Full Project Audit: Blueprint v1.7 and PRD v1.1 against the real code

| | |
|---|---|
| Date | 2026-10-26 |
| Branch | `docs/full-project-audit` (from `main` @ `4574ad4`) |
| Compared against | `specs/scholarship-ai-assistant-master-blueprint-v1.md` (v1.7, "BP"), `specs/scholarship-ai-assistant-prd-v1.md` (v1.1, "PRD"), the Part B agent PRD inside the Blueprint, `specs/001-*/contracts/openapi.yaml`, `specs/002-*/contracts/openapi.yaml`, `docs/blueprint-gap-analysis.md` |
| Type | Read-only audit. No application code changed. No Gemini calls. No network calls. No tests run. No database connection. |

## 0. How this was produced, and what to trust

**What I did.** I read the Blueprint and PRD in full, then opened the code for every item below. I judged each item from the file, never from its name or its docstring alone. Where a docstring claims something the code does not do, the code wins and I say so.

**The only things I executed** were two read-only Python snippets that import the app and print (a) the route list from `app.openapi()` and (b) the table and column list from the SQLAlchemy metadata. Neither connects to a database or to Gemini. I also diffed the live route list against both OpenAPI contract files.

**Not verified by running.** Live database contents (the gap analysis' counts of 34 scholarships, 0 deadlines, 0 users are taken from it, not re-checked). Test pass/fail status. The VM's real `.env`. Anything marked *(by reading)* is my reading of the code, not an observed run.

**Marks.**

| Mark | Meaning |
|---|---|
| MATCHES | The code does what the Blueprint or PRD says. |
| WEAKER | It exists and works, but does less than the document says. |
| NAME ONLY | It is named like the document's thing but is much simpler (for example a "workflow" that is a plain chain, an "agent" that is one function). |
| NOT REACHABLE | It is built, but no API route, scheduler or other live code path calls it. |
| MISSING | It is not built. |

For PRD requirement IDs (section 14) I use the three words you asked for: Met, Partly met, Not met.

**Short pointer to the end.** The comparison with the gap analysis is section 15, the count of each mark is section 16, and the 15 biggest problems are section 17.

---

## 1. Agents (Blueprint §7, §9, §21; PRD Part B)

First, the headline fact: **none of the five "agents" is an agent loop.** There is no dependency on the OpenAI Agents SDK in `backend/pyproject.toml`. No file in `app/` imports `agents`, `openai`, `langchain`, or any MCP library (`grep` over `app/`: the only `langchain` and `openai` hits are none; the only `mcp` hits are an enum value and comments). The only LLM access is `app/core/llm.py:33` `generate_text` (one `generate_content` call, no tools, no loop, no retry, no budget) and `:48` `embed_texts`.

| ID | Blueprint / PRD says | What the code actually does | Proof | Mark |
|---|---|---|---|---|
| A-01 | **Main agent** (§7.1, B1): LLM that parses compound constraints ("fully funded IT Master's, GRE not required") and dispatches. "A rule-based router cannot robustly parse compound constraints." | A keyword router. No LLM. No constraint extraction. Intents are matched by substring lists. | `app/orchestration/main_agent.py:57-69` keyword tuples; `:104` `route_message`; docstring `:5` "Routing is deterministic and rule-based, not an LLM guess" | NAME ONLY |
| A-01b | Every request enters through the Main agent (§4 walkthrough, §6) | `handle_message` has **no caller** in `app/`. No route exists. Only `tests/integration/test_main_agent_routing.py` uses it. | `app/orchestration/main_agent.py:231` (def only); `app/main.py:65-81` (16 routers, none for it) | NOT REACHABLE |
| A-02 | **Research/Q&A agent** (§7.2, B2): decides retrieve vs research live, picks tools, Self-RAG reflection. | A fixed function: retrieve chunks, one LLM call, one lexical overlap check, label. No tool choice, no loop. | `app/agents/research_qa/agent.py:28-52`; reached by `POST /scholarships/{id}/qa` (`app/api/qa.py:18`) | NAME ONLY |
| A-02b | Weak grounding **escalates to live official-source research**; the LLM draft is "overridden" (B2 Hard-Gated Zone, §12, §5.1) | No escalation. Weak grounding returns "I could not confirm this". No fetch, no search from Q&A. | `agent.py:52-54` returns `_unknown_answer()`; no import of any fetch tool in the file | MISSING |
| A-02c | Q&A "Verified" must not overstate (B2: no unsupported claim as fact) | Q&A uses the bare `ground_check`, whose own docstring says it passes numeric overstatement and must not be used for generation. Q&A answers are generated text, full of numbers and dates. | `agent.py:44` calls `ground_check`; `app/tools/ground_check.py:32,37-45` "Known limitation — do not use this for generation grounding" | WEAKER |
| A-03 | **Discovery agent** (§7.3, B3): LLM plans strategies, picks connectors, iterates when a source fails or is thin, triages candidates. | A plain function: build a (dimension, source) grid, loop over it, call the tools, hand candidates to ingestion. No LLM decision anywhere in the agent; the only LLM use is inside the `extract_listing` and `classify` tools. No iteration on thin results. | `app/agents/discovery/agent.py:117-154` (plan), `:303-339` (loop); reached by `POST /discovery` (`app/api/discovery.py:37`) | NAME ONLY |
| A-03b | Discovery hard gates (B3): no self-authorised sources, failed fetch is never "none found", coverage always attached | All three hold. Agent only reads `get_active_sources`/`get_source_by_id`; failures go to `fetch_errors` and `source_fetch_log`; coverage is always computed. Enforced by an AST test. | `agent.py:141,250-298,337`; `tests/agents/test_discovery_guardrails.py` | MATCHES |
| A-04 | **Application/Submission agent** (§7.4, B4): dynamic planning, asks the user for each missing item, presents **each** result for approval, persisted progress. | A three-branch rule table over the recomputed checklist: `action_required`, `awaiting_submission_approval`, `ready_to_submit`. No LLM. It cannot generate a CV, request a document or present a draft; it only reports. The one approval is the final-submission approval. | `app/agents/application/agent.py:1-5` "NO LLM CALL ANYWHERE IN THIS MODULE"; `:63-108`; reached by `POST /applications/{id}/assistant/next-step` (`app/api/application.py:104`) | NAME ONLY |
| A-04b | Button and agent produce the same result, no duplicated logic (§7.4 dual-mode, B4 parity) | Both call `run_app_plan` and `run_submit_prep`. | `agent.py:63,85`; `app/api/application.py:89-100` | MATCHES |
| A-04c | Approval at **every** gate (§7.4, B4), including after CV and SOP drafts | Only one approval type exists (`submission_approvals`, scope plus content fingerprint). A generated CV/SOP has no approve, reject or edit state. | `app/models/application.py` (`SubmissionApproval`); `app/api/generation.py:53,77` return the draft directly | WEAKER |
| A-05 | **Matching agent** (§7.5, B5): an agent reasoning over soft and fuzzy criteria with tools (profile reader, requirement reader, fuzzy resolver). | One LLM call with a JSON prompt and no tools, then deterministic assembly. The guardrail design is sound; the "agent" part is a single call. | `app/agents/matching/agent.py:76-112` (call at `:92`); `:135-168` | NAME ONLY |
| A-05a | Guardrail (a) hard-constraint lock | Hard outcomes are computed first, passed in, echoed unchanged. Any soft criterion with a colliding name is dropped. | `matching/agent.py:98-99,135-168`; `schemas/match.py` validator | MATCHES |
| A-05b | Guardrail (b) schema-locked output | Frozen Pydantic `MatchVerdict`. | `app/schemas/match.py:43` | MATCHES |
| A-05c | Guardrail (c) evidence-required: unsupported claims "rejected by an output guardrail" | A `met`/`unmet` result needs a non-empty `evidence` string, nothing more. The string is never checked against the scholarship data. A made-up quote passes. | `matching/agent.py:105-107` | WEAKER |
| A-05d | Guardrail (d) no-guess, (e) bounded tools (no web), (f) deterministic ranking | Hold. No fetch import (statically tested); ranking is in `services/ranking.py`. | `matching/agent.py:18-19`; `app/services/ranking.py:28-30`; `tests/agents/test_matching_guardrails.py` | MATCHES |
| A-05e | An **exclusion** that the scholarship triggers means **Not a Match** (§18 table, PRD FR-MATCH-1) | `exclusions_triggered` is a list of strings from the LLM that is copied into the verdict and never read again. It does not change `match_strength` or `eligibility_verdict`. | `matching/agent.py:111` (collected), `:115-132` (derivation ignores it), `:160` (echoed only); `grep exclusions_triggered app/` shows no other reader | MISSING |
| A-06 | Agents are built on the **OpenAI Agents SDK** with handoffs and guardrails (§21, §6, §7.0) | The SDK is not a dependency. No handoffs. Guardrails are hand-written Python. | `backend/pyproject.toml:6-30`; no import in `app/` | MISSING |
| A-07 | Agent loop **plan, act, check, re-plan**; human only at gates (§7.0) | No agent runs a loop. The only loops are: discovery's for-loop over plan steps, and CV/SOP "draft again if the JSON was invalid" (2 attempts). | `discovery/agent.py:327`; `cv_gen/graph.py:109` | MISSING |
| A-08 | No sub-agents in MVP (§8, FR-AGENTIC-2) | None exist. | repo-wide | MATCHES |
| A-09 | Human gates that exist (§7.0): candidate-source approval; final submit approval | Both are enforced in code and tests, though the candidate gate has no role check (see S-03). | `app/api/sources.py:59,82`; `app/workflows/submit_prep/graph.py:160-170` | MATCHES |

**Count of real LLM agent loops in the system: 0 of 5.** LLM calls sit in: Q&A (1 call), Matching (1 call, only when the profile has soft or exclusion criteria), and inside tools used by Discovery (`extract_listing`, `classify`). Main and Application make none.

---

## 2. Workflows (Blueprint §10)

All the LangGraph workflows are **straight lines**: every one is `add_edge` from node to node with one exception (`source_validate` has a conditional edge). No workflow uses a checkpointer, an `interrupt`, or a human-in-the-loop pause (`grep -rnE "checkpoint|interrupt|MemorySaver" app/` returns nothing). They are plain function chains with LangGraph as the runner.

| ID | Blueprint says | Code does | Proof (and the route or scheduler that runs it) | Mark |
|---|---|---|---|---|
| W-01 | `url_match`: `official_fetch`, extract, **verify**, **normalize**, load profile, match, gaps, attribute | Raw `fetch_url` (no registry, no official check), extract, save, index into Qdrant, hard constraints, match, save match. No verify node, no normalize node. The saved scholarship is `UNVERIFIED`. The result calls whatever URL the user pasted the "official_source". | `app/workflows/url_match/graph.py:62,72,95,170-187`; run by `POST /scholarships/match-url` (`app/api/url_match.py:20`) | WEAKER |
| W-01b | (Efficiency) | The LLM extraction runs **before** the "is this URL already stored?" check, so a repeat of a known URL still spends one LLM call and one Qdrant ingest. | `url_match/graph.py:72` (extract) comes before `:80` (lookup) *(by reading)* | WEAKER |
| W-02 | `doc_pipeline`: receive, identify_type, parse, extract_fields, associate, satisfy_check, flag_missing | Same node names, linear. `identify_type` only checks the file ends in `.pdf`; the "type" is whatever the user declared. PDF only. Only four declared types can satisfy any requirement. Scanned PDFs get a "needs OCR" flag, no OCR. | `app/workflows/doc_pipeline/graph.py:69-74,113-118,224-241`; run by `POST /applications/{id}/documents` (`app/api/documents.py:41`) | WEAKER |
| W-03 | `cv_gen`: gather, select_format, draft, ground_check, render | Real chain; the grounding gate really blocks the whole run on one bad claim. Output is a `.txt` file; "Europass" only changes a word in the heading. | `app/workflows/cv_gen/graph.py:226-337,321,329`; run by `POST /applications/{id}/generate/cv` | WEAKER |
| W-03b | "If Europass is required, generate to that spec" (§16, FR-GEN-1) | The format is a label. The renderer writes `CV (europass)` followed by bullet lines for both formats. | `cv_gen/graph.py:321-329` | NAME ONLY |
| W-04 | `sop_gen`: gather, outline, draft, ground_check, polish, render | All six nodes exist; the scholarship's own SOP questions are used when a requirement key names them. | `app/workflows/sop_gen/graph.py:74,338-354`; `POST /applications/{id}/generate/sop` | MATCHES |
| W-05 | `app_plan`: load, derive tasks, map documents, sequence, checklist | Four nodes, deterministic, idempotent task replace, no LLM. | `app/workflows/app_plan/graph.py:112-123`; `POST /applications/{id}/plan` | MATCHES |
| W-06 | `submit_prep`: compile case, **readiness check**, [APPROVAL GATE], **map to portal**, [FINAL GATE], (future) send | Load, assemble, fingerprint, check a stored approval, build a staged list of document ids. No readiness node (the agent calls `app_plan` first instead). No portal mapping. The "gates" are a database lookup, not a pause. | `app/workflows/submit_prep/graph.py:201-214`; reached only via `POST .../assistant/next-step` | WEAKER |
| W-07 | `ingestion`: extract, normalize, classify, dedup, **verify(official)**, store, index; failures retried with backoff | Normalize, classify, dedup, derive_conflicts, verify, conflict_resolution, store, index. `verify` only computes a lifecycle status from dates and a flag (see V-01). `index` needs `raw_html`, which discovery and monitoring never pass, so discovered records are never indexed. The retry wrapper is given `sleep=lambda _delay: None`, so ingestion retries are immediate with no backoff. | `app/workflows/ingestion/graph.py:126,312-335,641-650,653-673` | WEAKER |
| W-07b | Pipeline ends with **MATCH** (§31: INDEX then MATCH) | There is no match step. Discovered records are never matched to the user's profile. | `ingestion/graph.py:664-672` ends at `index`; `app/agents/discovery/agent.py:157-193` | MISSING |
| W-08 | `source_validate`: reachability, type and reliability checks, **[HUMAN APPROVAL GATE]**, add to registry | A real small LangGraph graph. The "gate" is a boolean argument (`approved=True`). It has **no caller** outside tests; the live approve route is a separate endpoint. It uses raw `fetch_url` with no SSRF guard. | `app/workflows/source_validate/graph.py:56-61,79-90,96` ; callers: tests only | NOT REACHABLE |
| W-09 | `source_monitor`: scheduled, load approved sources, fetch, diff, ingestion, notify (LangGraph, §10) | **Plain Python**, by decision. Real advisory locks, health transitions, lifecycle upkeep, alerts. Runs from APScheduler and `POST /monitoring/runs`. | `app/workflows/source_monitor/graph.py:3-5` "Plain Python orchestration, NOT LangGraph and NOT an agent, despite living in a graph.py"; `:542`; `app/scheduling/scheduler.py:58-69` | MATCHES |
| W-10 | Workflows are **checkpointed**, support **resume across sessions** and **human-in-the-loop pauses** (§10, §7.0) | None of the nine do. State lives only in one `invoke` call. | no `checkpointer=`/`interrupt` anywhere in `app/workflows/` | MISSING |
| W-11 | Each node has typed state; failures retry with backoff | Typed `TypedDict` state everywhere. Backoff exists for HTTP (`retry_policy.py:14-16`: 3 tries, 2s base, 8s cap) but not for ingestion (see W-07). | `app/sources/retry_policy.py:14-16` | WEAKER |
| W-12 | Dual mode: buttons and agent call the same workflows | Holds for plan and submit_prep. | `app/agents/application/agent.py:63,85` | MATCHES |

---

## 3. Tools (Blueprint §11) and MCP (§15)

| ID | Blueprint says | Code does | Proof; who calls it | Mark |
|---|---|---|---|---|
| T-01 | `api_connector`: approved scholarship API client | The registry gate and logging are real. The transport is a placeholder: `GET https://{domain}?q=...` expecting `{"results": [...]}`. No provider is onboarded and no seeded source has `access_method=api`. | `app/sources/connectors/api_connector.py:58` docstring: "Placeholder transport … A real per-source request shape belongs to a later slice; this exists only so the connector is exercisable"; called by discovery for API sources (none exist) | NAME ONLY |
| T-02 | `official_fetch`: registry-governed official fetch **(MCP)** | Real, gated by `status=active`, retried, logged, with redirect checks. Transport is plain `httpx`. | `app/sources/connectors/official_fetch.py:1-47` ("No MCP fetch-server provider has been chosen yet … reuses that existing httpx-based `fetch_url`"); called via `listing_fetch_tool` from discovery and monitor | MATCHES |
| T-02b | Per-source fetch rules in the registry are honoured (§29: "per-source rules"; `docs/SOURCES.md`: a connector "must not fetch this domain until robots.txt/ToS have been reviewed and `robots_checked` flipped to true") | `robots_checked`, `min_request_delay_seconds`, `max_pages_per_run`, `allowed_path_prefixes` and the per-source `user_agent` are stored but **never read** by any code. All five seeds have `robots_checked: false` and two of them (EACEA, HEC) are fetched every monitoring run. `web_fetch` uses its own fixed User-Agent. | seed lines `app/data/seeds/seed_sources.yaml:31,61,89,119,147`; `grep -rn "robots\|min_request_delay\|allowed_path_prefixes\|max_pages_per_run" app/` finds none outside models/schemas; `app/tools/web_fetch.py:10` | MISSING |
| T-03 | `web_fetch`: MCP fetch server | Plain `httpx.get(..., follow_redirects=True)`. No size limit, no SSRF guard. Used raw by `url_match`, `source_validate` and candidate validation. | `app/tools/web_fetch.py:26-33` | WEAKER |
| T-04 | `search`: MCP search server, approved only | The domain filter is real; the backend is not. The default raises `NotImplementedError`. Nothing calls it. | `app/tools/search.py:40` ("No MCP search-server provider has been chosen yet"); `grep search_tool app/` shows only its own file | NOT REACHABLE |
| T-05 | `classify`: rules plus LLM for fuzzy | Rules use 32 country names and 20 fields and short keyword lists, substring match (`"uk"` in longer words). The ingestion call joins the candidate's raw values into one string (`"fully_funded"` has an underscore, so `\bfully[- ]funded\b` cannot match it). Any unresolved dimension triggers an LLM call, so **I expect about one extra Gemini call per newly ingested candidate** *(by reading, not measured)*. LLM failure is swallowed silently. | `app/tools/classify.py:55-62,122-147` (`except Exception:` at `:140`); `app/workflows/ingestion/graph.py:172-173` | WEAKER |
| T-06 | `pdf_parse`: text, pages, tables (PyMuPDF) | Returns text, pages, tables with PyMuPDF; corrupt, encrypted and empty PDFs are distinct outcomes. | `app/tools/pdf_parse.py:2,25,29` | MATCHES |
| T-07 | `extract_requirements`: schema-validated JSON, **reject-and-retry on invalid** | One LLM call. Invalid JSON raises and is not retried. Text is cut at 20,000 characters. The model labels its own fields `value_status="known"` and `confidence="verified"`, and nothing checks them against the page. | `app/tools/extract_requirements.py:88-94` (`:91` truncation); `url_match/graph.py:99-107` copies them into `requirements` | WEAKER |
| T-08 | `extract_profile`: CV text to Profile JSON | Works for the top-level fields only (name, nationality, residence, current degree, target level, fields, countries). Education records, GPA, tests, experience are not extracted. It is a suggestion; nothing is auto-saved. | `app/tools/extract_profile.py:18-23`; `POST /profile/import-cv` (`app/api/profile.py:82`) | WEAKER |
| T-09 | `embed`: local Hugging Face sentence-transformer (A2 default) | Gemini `gemini-embedding-001`, 768 dimensions, through the free-tier API. Departure acknowledged in BP §21 and §46. | `app/core/llm.py:12-13,48-57`; `app/tools/embed.py` | WEAKER |
| T-10 | `rerank`: HF cross-encoder, optional | A cosine reranker over Gemini embeddings, off by default. **Nothing calls it**; retrieval never reranks. | `app/tools/rerank.py:1-12` ("No HF cross-encoder dependency is installed … this reuses Phase 1's existing embedding infrastructure"), `:40`; `grep "rerank(" app/` finds only the definition | NOT REACHABLE |
| T-11 | `ground_check`: (claim, sources) to {supported, evidence}; "LLM + retrieval" | Deterministic word-overlap, no LLM (by constitution). Threshold 0.6 for Verified and 0.3 for Inferred in Q&A. A stricter `verify_claim_grounded` (adds a number check) is used only by CV/SOP. | `app/tools/ground_check.py:32-93`; `agents/research_qa/agent.py:13-14` | WEAKER |
| T-12 | "All external calls are confined to tools" (§11) | LLM calls are also made directly by agents and workflows: `matching/agent.py:92`, `research_qa/agent.py` (via `generate_text`), `cv_gen/graph.py:110`, `sop_gen/graph.py:160`. The CV import does PDF parsing inside the router (`api/profile.py:22`). | as listed | WEAKER |
| T-13 | Extra tools not in §11 | `extract_listing`, `listing_fetch`, `detect_candidate_links`, `extract_document_fields` exist and are used. Listing names are grounded at threshold 1.0, but this is a bag-of-words test: each significant word only has to appear *somewhere* on the page, not as a phrase. The other listing fields (deadline, country, degree, funding) are never grounded. Listing text is cut at 20,000 characters. | `app/tools/extract_listing.py:112,141` | WEAKER |
| M-01 | **MCP** (§15, §21): fetch, search and API servers; "registry-governed MCP + connectors" | **No MCP client or server exists.** `AccessMethod.MCP` is an enum value with no code behind it. | `app/models/source.py:47`; comments at `official_fetch.py:8`, `search.py:7` | MISSING |

---

## 4. Services (Blueprint §6 and elsewhere)

| ID | Blueprint says | Code does | Proof | Mark |
|---|---|---|---|---|
| S-01 | `profile` service: CRUD, criteria classify, missing info | Real, deterministic. Missing info is a fixed list of six dimensions, not tied to what a given scholarship needs. | `app/services/profile.py:45-109` | MATCHES |
| S-02 | `normalize`: "deterministic mapping to canonical model" (§9) | It only wraps each value as known/unknown with a confidence. It does not canonicalise anything (countries, degree names, dates, currencies). `usa` and `USA` stay different. | `app/services/normalize.py:30-67` | NAME ONLY |
| S-03 | `classify` | See T-05. | `app/services/classify.py` | WEAKER |
| S-04 | `dedup`: deterministic key (name+university+intake) then vector similarity | The key only applies when **all three** parts are non-empty. Listing candidates have no university or intake, so the key never matches. The DB lookup is by exact normalised name, so near-duplicates with different names are never compared. A vector near-duplicate returns `flag_for_review`, which **nothing reads**: the record is then inserted as a new row. | `app/services/dedup.py:73` (`if all(candidate_key)`), `:97`; `app/data/repositories/scholarship_repo.py:44-54`; `grep -rn FLAG_FOR_REVIEW app/` finds only dedup.py | WEAKER |
| S-05 | `ranking`: deterministic formula | Sort by (eligibility rank, strength rank). A "Not" verdict ranks last. | `app/services/ranking.py:28-30` | MATCHES |
| S-06 | `source_registry` service: CRUD, governance, seed | Real; separate candidate table; active-only read gate. | `app/services/source_registry.py`; `source_repo.py:50-65` | MATCHES |
| S-07 | `coverage`: configured, active, checked, failed, last checked, **countries, universities, providers covered**, candidates, verified, gaps | Computes configured/active/checked/failed/last-checked/pending candidates/verified count/countries covered/gaps live. **No universities-covered or providers-covered field.** No per-region breakdown. | `app/services/coverage.py`; `app/schemas/discovery.py:29-48` (`countries_covered` only); route `GET /coverage` | WEAKER |
| S-08 | `verification`: verify against the official source | Computes a lifecycle label from dates and one boolean. Nothing in the code ever sets that boolean to true (see V-01). The service never opens an official page. | `app/services/verification.py:34-62` | NAME ONLY |
| S-09 | `conflict_resolution`: official > recent > reliable, else mark conflicting | The policy function is real and well tested. In the live pipeline it only runs when dedup decides `merge_with_existing` AND a field snapshot exists. Given S-04, that rarely happens for listing candidates; the monitoring path deliberately skips it. | `app/services/conflict_resolution.py`; `ingestion/graph.py:254-261,341-342` | WEAKER |
| S-10 | `hard_constraints`: GPA, degree, nationality, deadline, mandatory tests, plus country, field, funding, language, spouse per `profile_criteria` dimensions (§14 lists 10 dimensions) | Evaluators for **GPA, nationality, degree, test** plus a universal deadline check. The other dimensions (country, field, funding, language, spouse_dependent, other) always return `unknown`. GPA ignores `gpa_scale`. Nationality is an exact string match. `float(requirements[0].value)` and `float(required_score)` raise `ValueError` if the stored value is text like "3.0 or equivalent" *(by reading)*, which would be an HTTP 500. | `app/services/hard_constraints.py:58,154,167-172,179-199` | WEAKER |
| S-11 | `lifecycle`: nine states | Real and deterministic. | `app/services/lifecycle.py:50-129` | MATCHES |
| S-12 | `auth` service (§9: "standard JWT logic") | See section 8. | `app/api/auth.py`, `app/core/security.py` | MATCHES |
| S-13 | Not in the Blueprint but built: `readiness`, `requirement_satisfaction`, `listing_diff`, `alerts`, `retention`, `candidate_validation`, `submission_approval` | All exist and are reached. `requirement_satisfaction` normalises GPA to a 4.0 scale; `hard_constraints` does not, so the two modules disagree about the same number. | `requirement_satisfaction.py:74-75` vs `hard_constraints.py:58-60` | WEAKER |

---

## 5. RAG and vectors (Blueprint §12, §13)

| ID | Blueprint says | Code does | Proof | Mark |
|---|---|---|---|---|
| R-01 | Ingest, parse, chunk, **metadata**, embed, Qdrant; metadata includes `section_type`, `degree_level`, `country` | Load HTML, 200-word chunks with 40 overlap, Gemini embed, upsert. Payload is only `scholarship_id`, `source_url`, `text`, `retrieved_at`. | `app/rag/embed.py:20-37`; `app/rag/chunk.py` | WEAKER |
| R-02 | Retrieval: semantic plus metadata filtering (this scholarship, only "family" sections) | Filter on `scholarship_id` only. No minimum score. No section filter. | `app/data/vectors/qdrant_client.py:94-111`; `app/rag/retrieve.py:17` | WEAKER |
| R-03 | Optional rerank | See T-10. | | NOT REACHABLE |
| R-04 | Qdrant collections: "scholarship page/PDF chunks, program descriptions, user-document chunks" | One collection, `scholarship_chunks`. No program descriptions. User documents are parsed into `parsed_meta` in Postgres and never vectorised. | `qdrant_client.py:23` | WEAKER |
| R-05 | Re-ingesting a page replaces its old chunks (implied by "freshness") | Every upsert uses new random UUIDs (`str(uuid.uuid4())`) and never deletes. `url_match` re-ingests on every call, so chunks pile up and stale page text stays retrievable. | `qdrant_client.py:85`; `url_match/graph.py:120` *(by reading)* | WEAKER |
| R-06 | INDEX step for every ingested record (§31) | The index node needs `raw_html`. Discovery and monitoring call `run_ingestion` without it, so **no discovered scholarship has any vectors**. Q&A can only answer for url-matched pages. | `ingestion/graph.py:642`; `discovery/agent.py:171-177`; `source_monitor/graph.py:281-286` | NOT REACHABLE |
| R-07 | Grounding-check loop (Self-RAG) | `app/rag/grounding.py::check_grounding` was written for this and **nothing calls it** (its docstring says Q&A and CV/SOP consume it; Q&A calls `retrieve` and `ground_check` directly). | `app/rag/grounding.py:20`; `grep check_grounding app/` shows only the def | NOT REACHABLE |
| R-08 | Qdrant is not the source of truth for fresh facts | Held: facts come from Postgres. | `qdrant_client.py:1-5` | MATCHES |
| R-09 | Vectors in Qdrant Cloud work in production | Per the repo's own ops record, the Qdrant Cloud endpoint reset connections on 2026-10-22 and the smoke tests failed ("Open, owner action"). While that is true, Q&A returns an error for every question. | `docs/operations-hardening.md:283,334` | WEAKER |
| R-10 | Three stores: Postgres, Qdrant, object storage (§13) | Postgres and Qdrant as described; documents on local disk through a storage Protocol (S3 later, assumption A6). | `app/data/files/storage.py:1-12` | MATCHES |

---

## 6. Database (Blueprint §14)

27 tables exist (dump from the SQLAlchemy metadata). Ten Alembic revisions form one linear chain from `fed0167c65c9` to head `f2b8d4a6c1e3`.

| Blueprint table | Real table | Difference | Mark |
|---|---|---|---|
| `users` | `users` | Same columns. **No role or operator column.** | MATCHES |
| `profiles` | `profiles` | `country` is `country_of_residence`; adds `target_fields`, `target_countries`. No age, spoken languages, finances or dependants. | MATCHES |
| `education_records`, `test_scores`, `experience` | same | Same columns. | MATCHES |
| `profile_criteria`, `missing_info` | same | Same columns; dimensions enum has 10 values as in §14. | MATCHES |
| `scholarships` | `scholarships` | `funding_type[full/partial/other]` is the 7-value `funding_status` (PRD OD-6). `verification_status` has no `listed` value yet (planned, feature 004). No unique constraint on name or official URL. | MATCHES |
| `universities`, `programs` | same | **Exist, but nothing reads or writes them** (`grep` finds the models only). `scholarships.university_id`/`program_id` are always NULL. | NOT REACHABLE |
| `scholarship_fields` | same | `evidence_snippet` and `last_verified_at` columns exist; no code writes `evidence_snippet`. `source_id` is not a foreign key. | WEAKER |
| `requirements`, `funding_details` | same | Same columns. Only url-matched scholarships ever get rows (discovery and monitoring do not pass requirements). No evidence column on requirements. | WEAKER |
| `scholarship_sources` | same | Same; no uniqueness on (scholarship, source, url). | MATCHES |
| `source_registry` | same | Has every §14 column plus `listing_page_url`, `freshness_window_days`. | MATCHES |
| `candidate_sources` | same | Same columns. | MATCHES |
| `source_fetch_log` | same | Same columns plus `fetched_url`, `used_homepage_fallback`, `monitoring_run_id`. The `timeout` status value exists in the enum but **no code ever writes it**. | WEAKER |
| `applications`, `application_documents`, `generated_documents`, `tasks`, `matches` | same | Same; extras (`checksum`, `source_trace`, `readiness_label`). Users can create several applications for the same scholarship. | MATCHES |
| `audit_log(id, user_id, action, entity, at)` | **none** | The table does not exist. | MISSING |

**Extra tables not in the Blueprint (5):** `alerts`, `alert_preferences`, `candidate_source_validations`, `monitoring_runs`, `submission_approvals`. Reasonable, and all are used.

**Value-status discipline (§14).** `value_status` and `confidence` exist on fields, requirements and funding details. But for url-matched pages the LLM fills them itself (T-07), so "known/verified" there is the model's claim, not a checked fact.

---

## 7. API (Blueprint §5.1; OpenAPI contracts)

**Route comparison, measured.** The live app exposes 38 operations. The two contracts together define 38 (25 in 001 plus 13 in 002). Matching on method and path: **no live route is missing from the contracts and no contract route is missing from the app.** Only the 002 contract has a drift test (`tests/api/test_openapi_contract_002.py`); the 001 contract's request and response shapes are not tested against the app.

| Capability (BP §5.1) | Route that exists | Input / output / next action vs the contract row | Proof | Mark |
|---|---|---|---|---|
| Find Scholarships | `POST /discovery` (no body, uses the saved profile) | Output is a list of scholarship ids with source, type and status, plus coverage. It is **not ranked**, **not matched** to the profile, and the same source's items are appended once per plan step, so one source can appear several times *(by reading)*. The profile only chooses which sources to visit; the country filter is an exact match against seed codes like `DE`. Synchronous. | `app/agents/discovery/agent.py:141,300`; `app/api/discovery.py:37-60` | WEAKER |
| Match My Profile | `POST /scholarships/{id}/match`, `GET /matches` | One scholarship at a time. There is no "rank all stored scholarships" call. `GET /matches` returns every historical run (one row per match call), not one per scholarship. | `app/api/matching.py:18-56` | WEAKER |
| Match a URL | `POST /scholarships/match-url` | Works end to end. See sections 8 and 10 for the SSRF and unverified-extraction problems. | `app/api/url_match.py:20-39` | WEAKER |
| Ask Q&A | `POST /scholarships/{id}/qa` | Returns `answer_text`, `confidence`, `source_url`, `last_verified_at`, `evidence_snippet`. One generic schema, not intent-specific (§34). No live-fetch escalation. | `app/api/qa.py:18`; `app/schemas/qa.py` | WEAKER |
| Application Planner | `POST /applications/{id}/plan` | Matches the contract row. | `app/api/application.py:89` | MATCHES |
| Documents | `POST /applications/{id}/documents` | PDF only. No file-size limit: the whole file is read into memory. | `app/api/documents.py:57` | WEAKER |
| CV Generator | `POST /applications/{id}/generate/cv` | Returns `file_ref` (an internal storage key) and `source_trace`. **There is no route to download the generated file.** | `app/api/generation.py:53`; route list | WEAKER |
| SOP / Motivation | `POST /applications/{id}/generate/sop` | Same as CV. | `generation.py:77` | WEAKER |
| Application Assistant | `POST /applications/{id}/assistant/next-step` | Reports the next step; cannot run it. | `app/api/application.py:104` | WEAKER |
| Application Tracker | `GET /applications` | Lists applications with their saved checklists. "Saved scholarships", deadlines view and status beyond the label are absent. | `app/api/application.py:55-85` | WEAKER |
| Dashboard | none | Saved scholarships, deadlines, next actions: no route. | route list | MISSING |
| Auth, Profile | `/auth/*`, `/profile*` | As specified. | `app/api/auth.py`, `app/api/profile.py` | MATCHES |
| "Every request enters through the Main Agent" (§4) | none | No orchestrator route. | A-01b | MISSING |
| Async job pattern for long calls (§5 rules, A7) | Only `POST /monitoring/runs` (202 plus background task) | Discovery, Q&A, matching, generation, URL match are all synchronous requests. | `app/api/monitoring.py:64-78` | MISSING |
| Error taxonomy: provider failure | Only `doc_pipeline` turns a Gemini API error into a friendly flag. Every other LLM route (match-url, match, Q&A, discovery, generate, import-cv) lets the error escape as an HTTP 500. | `grep -rn APIError app/` finds one handler | `app/workflows/doc_pipeline/graph.py:143` | MISSING |
| Contracts vs live routes | 38 vs 38, exact | Paths and methods agree; 001 body shapes are not drift-tested. | script diff (section 0) | MATCHES |

---

## 8. Auth and security (Blueprint §19, §20; PRD NFR-SEC)

| ID | Blueprint / PRD says | Code does | Proof | Mark |
|---|---|---|---|---|
| X-01 | Sign up, log in, log out; hashed passwords; JWT (§20) | Done: bcrypt, HS256 JWT with 60-minute expiry. Logout does nothing server-side and says so. | `app/core/security.py:8-35`; `app/api/auth.py:49-55` | MATCHES |
| X-02 | Input validation; strong credentials | No password rules at all (`password: str`, not even a minimum length). `JWT_SECRET_KEY` has no strength check, so the `.env.example` placeholder `change-me-to-a-random-secret` would be accepted. | `app/schemas/auth.py:9`; `app/core/config.py:18`; `.env.example:9` | WEAKER |
| X-03 | Per-user isolation; "every query scoped by `user_id`", enforced at the repository layer (§19, NFR-SEC-1/3) | Holds: repositories take `user_id` and filter on it (`get_by_id_for_user`, `list_for_user`, document and alert getters); storage keys include the user id and reject `..`; many API tests check cross-user 404s. Scholarships are a shared catalogue by design. | `app/data/repositories/application_repo.py:29`; `document_repo.py:31`; `app/data/files/storage.py:36-80` | MATCHES |
| X-04 | Authorisation / access control / roles (§19; PRD FR-DISC-8 operator-only approval) | **No roles.** Any signed-up user can: approve or reject a candidate source, **trigger a monitoring run** (which calls the LLM for each monitored source), read `/metrics`, read all fetch logs. Signup is open. | `app/api/sources.py:59,82` ; `app/api/monitoring.py:70` ; `app/core/metrics.py:61`; `app/models/user.py` has no role column | MISSING |
| X-05 | URL-match fetch is safe | `POST /scholarships/match-url` fetches any `http(s)` URL the caller supplies, follows redirects, with **no check for private, loopback or link-local targets**. The server runs on a cloud VM, so a signed-up user can make the server request internal addresses (for example the VM's metadata address) and get the extracted result back. The SSRF guard exists only for candidate validation. | `app/tools/web_fetch.py:26-33`; `url_match/graph.py:62`; guard only at `app/services/candidate_validation.py:83` | MISSING |
| X-06 | Secure file handling | Path traversal is blocked. But there is **no upload size or type limit** on documents or CV import; `import_cv` is `async def` yet runs a blocking PDF parse and a blocking LLM call inside the event loop; a bad PDF raises an unhandled error (500). | `app/api/documents.py:57`; `app/api/profile.py:20-26,82-90` | WEAKER |
| X-07 | Secrets via env, never in the repo | `.env` is untracked; a test scans for secrets; CI uses dummies. | `tests/test_no_secrets_in_repo.py`; `git ls-files` shows no `.env` | MATCHES |
| X-08 | Rate limiting, HTTPS (Phase 5) | Per-IP in-memory token bucket (120/min) behind Caddy with TLS; `/metrics` blocked at the proxy. Per process, and no per-user limit on the calls that cost LLM quota. | `app/core/rate_limit.py:1-12`; `Caddyfile:14-25` | WEAKER |
| X-09 | `audit_log` (§14) | No table and no audit writes (approvals are recorded, nothing else). | section 6 | MISSING |
| X-10 | Least-privilege DB role (§19) | Cannot be judged from the repo. | n/a | WEAKER |
| X-11 | Prompt-injection from fetched pages | Page text is passed to the LLM, but its output is constrained to JSON fields and grounded for names; sanitisation strips markup and script schemes from candidate fields. Requirements from url-match are not grounded (T-07). | `source_monitor/graph.py` `_sanitize_candidate`; `tests/sources/test_monitor_sanitization.py` | WEAKER |

---

## 9. Documents, CV/SOP, application planning and tracking (Blueprint §16, §17)

| ID | Blueprint says | Code does | Proof | Mark |
|---|---|---|---|---|
| D-01 | Document intelligence: ask for missing docs, accept uploads, parse, associate, compare, detect missing and inconsistencies, track readiness | Upload, parse, extract fields (LLM), associate, deterministic satisfaction, profile-inconsistency flags (`detect_profile_inconsistencies`). "Asking" for missing documents is the checklist label; no prompting. | `workflows/doc_pipeline/graph.py:119-175`; `app/services/requirement_satisfaction.py` | WEAKER |
| D-02 | Material types: transcript, certificate, passport, CV, Europass, SOP, motivation letter, personal statement, study plan, research proposal, recommendation guidance, certificates, test docs, portfolio, publications, work docs, character certificate, financial docs, forms | The enum lists 15 or so types (`models/document.py:20-42`). Only **CV and SOP** can be generated; none of the rest has a generator or a guidance flow. Only four declared types can satisfy a requirement. | `app/models/document.py:20-50`; `doc_pipeline/graph.py:69-74` | WEAKER |
| D-03 | CV/SOP grounded: "never fabricates achievements, experience, publications, grades" | A real gate: every drafted claim must overlap an allowed fact (numbers must match); one bad claim blocks everything. Weakness: the allowed-fact pool also includes the **scholarship's requirements** (`Scholarship requirement (gpa): gpa = 3.5`), so a claim that merely restates a requirement can be "grounded" against it. | `cv_gen/graph.py:215-223,281-306` | MATCHES |
| D-04 | Requirement-aware SOP: answer the university's exact questions | Questions are taken from requirements whose key contains `sop_question`, `motivation_question`, `essay_question` or `personal_statement_question`. Otherwise a generic question. Requires the requirement to exist, which discovered scholarships lack. | `sop_gen/graph.py:74` | WEAKER |
| D-05 | Planner: six labelled states (Complete, Missing, User-must-obtain, AI-can-generate, Needs-human-review, Needs-official-verification) | All six are produced by a pure function. | `app/services/readiness.py:162-186` | MATCHES |
| D-06 | Tracker: saved scholarships, applications, deadlines, missing docs, tasks, status, (future) interview stage | Applications plus their checklists; deadlines only as per-task `due_date`; no saved-scholarship list; no reminders. Interview stage is deferred by the PRD. | `app/api/application.py:55-85` | WEAKER |
| D-07 | Review, approve or edit generated drafts before they count (§7.4, §17.1 "human review") | No such state or endpoint. | `app/api/generation.py` | MISSING |
| D-08 | Submission always approval-gated; no autonomous send | Holds: approval is scope plus content fingerprint, append-only; a static test forbids any outbound-send code in `app/`. The fingerprint covers document ids and checksums, not the profile or requirements. | `app/workflows/submit_prep/graph.py:1-45`; `tests/api/test_no_live_submission.py`; `app/services/submission_approval.py` | MATCHES |

---

## 10. Matching (Blueprint §18; PRD FR-MATCH)

| ID | Blueprint says | Code does | Proof | Mark |
|---|---|---|---|---|
| MA-01 | Hard constraints decided deterministically, include nationality, GPA, degree, deadline, tests, (country, field, funding, language, spouse) | Only the profile's criteria **of kind hard_constraint** are evaluated, and only for GPA, nationality, degree, test, plus deadline. Fields on the profile (nationality, GPA) are **not** checked unless the user also created a matching hard-constraint criterion row. | `hard_constraints.py:179-199` | WEAKER |
| MA-02 | Missing or unknown data is surfaced, never guessed | Unknown requirement gives `unknown`. But `missing_information` is the profile's six global gaps copied onto every verdict, whether or not this scholarship needs them. | `agents/matching/agent.py:150-168`; `app/services/matching.py:25`; `profile.py:91-107` | WEAKER |
| MA-03 | A profile with nothing evaluated must not look eligible | With no hard-constraint criteria and no soft criteria, the verdict is **"Likely eligible / Possible match"** as long as the deadline has not passed. This is written into a test as the expected result. | `matching/agent.py:115-132`; `tests/services/test_hard_constraints_matrix.py:148-157` | WEAKER |
| MA-04 | Soft preference misses lower strength, never disqualify | Holds: strength is Strong if at least half of the decided soft criteria are met, else Possible. | `matching/agent.py:115-122` | MATCHES |
| MA-05 | Exclusions disqualify | See A-05e. | | MISSING |
| MA-06 | Two verdict layers (eligibility, strength) with the §18 object | Both present. `required_documents` and `remaining_actions` are always `[]`. | `matching/agent.py:165-166` | WEAKER |
| MA-07 | Criterion outcomes Pass / Fail / Unknown / **Inferred** (PRD FR-MATCH-6) | `INFERRED` exists in the enum, but no code produces it. | `schemas/match.py:30`; `matching/agent.py:101-109` | WEAKER |
| MA-08 | Country and nationality names and aliases (FR-MATCH-7) | Exact lower-case string equality. "Pakistani" does not equal "Pakistan". Planned for feature 005. | `hard_constraints.py:87` | MISSING |
| MA-09 | Hard constraint evidence comes from verified requirements | Requirements for url-matched pages are LLM-asserted (T-07); a wrong extracted GPA or deadline can produce a hard "Not eligible". | `url_match/graph.py:99-107` | WEAKER |
| MA-10 | Deterministic ranking | Holds (A-05d). | | MATCHES |

---

## 11. Source registry, discovery, pipeline, verification, failures, monitoring (Blueprint §29-§35)

| ID | Blueprint says | Code does | Proof | Mark |
|---|---|---|---|---|
| V-01 | **Verify against the official source** (§31, §32, FR-VERIFY-1/3) | No call site passes `official_source_confirmed=True` (`grep` finds none). New records are stored `UNVERIFIED`. The `VERIFIED` label is set by `mark_confirmed_by_monitoring` when the same listing item is seen a second time on an approved listing page. The scholarship's own page is never opened for discovered records. ADR-0013 redefines the target; it is not built. | `app/data/repositories/scholarship_repo.py:91-106`; `source_monitor/graph.py:315-316`; `ingestion/graph.py:554` | NAME ONLY |
| V-02 | A record's official URL is its own official page (§14 `official_scholarship_url`) | Discovery and monitoring store `https://{source.domain}` (the source **homepage**) for every record. The extracted `detail_url` is dropped. | `discovery/agent.py:174`; `source_monitor/graph.py:281-286` | WEAKER |
| V-03 | Registry is human-defined; candidates are never authoritative; connectors reject non-active sources | Held and tested: separate tables, active-only gate, AST tests. | `models/source.py`; `source_repo.py:50-65`; `tests/sources/test_source_registry_compliance.py` | MATCHES |
| V-04 | Registry rules are enforced (per-source constraints, extraction rules) | See T-02b. `extraction_rules` is used only for `list_page_url` fallback. | `official_fetch.py:84-95` | MISSING |
| V-05 | Candidate discovery by following links; semi-automated validation; human approval | Link detection (7 English keywords, up to 5 per fetch), advisory validation (reachability, extractability, `.gov`/`.edu` signals; one LLM call per candidate), manual approve/reject. Approval has no role check (X-04). | `app/tools/detect_candidate_links.py`; `app/services/candidate_validation.py`; `app/api/sources.py` | WEAKER |
| V-06 | Multi-channel retrieval: APIs, MCP, approved search, official sites, aggregators as signal (§30.1) | Only registry-gated web listing pages work. API is a stub (T-01), search is unreachable (T-04), MCP is absent (M-01). Five seeded sources, all `web`; two have `discovery_role=true`. | `seed_sources.yaml`; section 3 | WEAKER |
| V-07 | Systematic query plan across 11 dimensions (§30.3) | Four dimensions: country, field, degree level, nationality. Country uses an exact match against ISO codes; the other three simply select every discovery source. No university, funding type, intake, requirement or deadline dimension. | `discovery/agent.py:130-154` | WEAKER |
| V-08 | Coverage engine reports measured coverage, never 100% (§30.4) | Holds, and the type system forbids a completeness claim. Missing universities-covered and providers-covered (S-07). | `schemas/discovery.py:46` `Literal[False]` | WEAKER |
| V-09 | Data-quality pipeline stages with provenance at every step | Stages exist (section 2, W-07); `scholarship_sources` provenance rows are written on store and merge. Required-field gate: only `name` blocks storage; the other required fields only add an "incomplete" flag row. | `ingestion/graph.py:63-74,602-614` | WEAKER |
| V-10 | Lifecycle: nine states with `retrieved_at`, `last_verified_at`, `deadline` | All nine exist and are used; `expired` needs a deadline and discovered rows have none (gap analysis count, not re-checked). | `app/models/scholarship.py:31-41`; `services/lifecycle.py` | MATCHES |
| V-11 | Failure and retry: failed fetch is never "none found"; retry with capped backoff; mark failing after N | HTTP path: 3 attempts with 2s to 8s backoff, logged on every attempt, health derived from the last N monitoring runs, `failing` flag and recovery. | `retry_policy.py:14-16`; `source_monitor/graph.py:379`; `source_repo.py:381` | MATCHES |
| V-12 | **LLM provider failure** recorded as a provider outcome, not a site failure (BP §33 v1.7, PRD FR-VERIFY-9, NFR-REL-4) | Not built (feature 003 is spec only). A provider error inside monitoring surfaces as an exception, is recorded as a failed check for that source and counts toward its failure streak. In API routes it is an HTTP 500. | `source_monitor/graph.py:397` (`_record_crashed_check`) and the `except Exception` branch in `run_source_monitor`; `app/core/llm.py` (no handling) | MISSING |
| V-13 | Cost-governed monitoring: unchanged page costs no LLM call; cadence from `update_frequency` (§35 v1.7) | Every run passes `bypass_cache=True`, so every monitored source is re-extracted by the LLM on every tick. `update_frequency` only sets the cache lifetime for *discovery*, not monitoring. The repository default interval is 60 minutes (`config.py:48`, `.env.example:23`); the Blueprint says it was raised to 360 on the VM, which I cannot see from the repo. | `source_monitor/graph.py:447`; `app/core/config.py:48` | MISSING |
| V-14 | Scheduled monitoring: detects new, changed, closing, reopening, stale (§35) | Built and locked per source. Only `deadline`, `funding_status` and `degree_level` can be diffed. | `app/services/listing_diff.py`; `source_monitor/graph.py:53` ("FR-MON-3 coverage gap (named, not hidden)") | WEAKER |
| V-15 | Re-match and notify (in-app, email) | Built with a re-match cap per run. | `app/services/alerts.py:187-339` | MATCHES |
| V-16 | Retention, health, metrics | Daily fetch-log pruning; `/sources/health`; Prometheus counters. | `scheduler.py:67`; `app/api/health.py:22`; `app/core/metrics.py` | MATCHES |
| V-17 | Structured per-intent response schemas (§34): eligibility, spouse, funding, apply | Schemas exist for Q&A (generic) and matching. No spouse, funding, eligibility-answer or how-to-apply schema. | `app/schemas/qa.py`, `schemas/match.py`; `schemas/plan.py` | WEAKER |

---

## 12. Testing and evals (Blueprint §25, §25.1)

Counts from `grep "def test_"`: 761 test functions. By folder: services 173, api 112, workflows 102, sources 91, data 81, integration 54, tools 46, core 42, agents 19, infra 13, scheduling 12, smoke 5, **rag 0**.

| ID | Blueprint says | What is really there | Proof | Mark |
|---|---|---|---|---|
| E-01 | Unit, API, agent, tool, document, source-governance, connector, ingestion, failure/retry, coverage, monitoring, security tests | All exist in volume. | `backend/tests/**` | MATCHES |
| E-02 | RAG and retrieval tests (recall, metadata filter) | `tests/rag/` contains only `__init__.py`. Retrieval is exercised through mocked integration tests; Qdrant filter behaviour is covered by one test file plus weekly live smoke. | `tests/rag/`; `tests/data/test_qdrant_client.py` | MISSING |
| E-03 | Anti-hallucination and extraction tests against real model behaviour | Every LLM call is mocked in per-commit CI. Real Gemini output is touched only by five smoke tests, run weekly or on demand (they are deselected by `addopts`). The quality of `extract_requirements`, `extract_listing` and `classify` on real pages is never measured. | `pyproject.toml` `addopts = "-m 'not smoke'"`; `.github/workflows/deep-ci.yml:5-9`; `tests/smoke/*` | WEAKER |
| E-04 | Matching verdict matrix on every build; guardrail violations break the build | Matrix has 10 cases; the CI eval gate requires 1.0. The cases never include an exclusion, a text-valued requirement, or an LLM soft path. | `evals/runner.py:36-52,70-77`; `tests/services/test_hard_constraints_matrix.py:159-170` | WEAKER |
| E-05 | Scored evals for **matching, Q&A, extraction, verification** (§25.1a) | Three: matching correctness (10 fixtures, hard constraints only: `build_verdict(..., [], [], [])`), Q&A groundedness (5 fixtures, LLM mocked), generation groundedness (5 fixtures). No extraction eval, no verification eval, no discovery eval. | `evals/runner.py:50,70-77`; `evals/cases/*` | WEAKER |
| E-06 | **The checker is itself graded** against a labelled set (§25.1b) | Not present. | `evals/` | MISSING |
| E-07 | CI **blocks a change that lowers scores** (§25.1c) | The gate compares to fixed thresholds (1.0, 0.5, 1.0), not to the previous score; there is no stored baseline. A drop from 0.9 to 0.55 on Q&A would pass. | `evals/runner.py:26,28,70-77`; `.github/workflows/ci.yml:75-78` | WEAKER |
| E-08 | Thresholds and checker-accuracy bar defined | Blueprint still says "NOT YET DEFINED". Thresholds live in code unratified. | BP §25.1; `runner.py:26-28` | WEAKER |
| E-09 | CI | One job: migrate, pytest, `pip-audit --strict` (two documented ignores), eval gate. No lint or type check. DB-dependent tests self-skip when no database is reachable (seven `pytest.skip` sites), which is safe in CI because CI provides Postgres. | `.github/workflows/ci.yml`; `tests/conftest.py:54,60` | MATCHES |

---

## 13. Frontend (Blueprint §5)

| ID | Blueprint says | Code does | Proof | Mark |
|---|---|---|---|---|
| F-01 | React + TypeScript + Vite; 13 screens (Auth, Dashboard, Profile, Find, Match My Profile, Match URL, Q&A, Planner, Documents, CV, SOP, Assistant, Tracker) | **No frontend.** No `package.json`, no `.tsx`, no Vite config anywhere (`find` over the repo). Deferred by decision (BP §22; feature 008). | repo-wide `find` | MISSING |
| F-02 | Typed API client generated from OpenAPI | None. | | MISSING |
| F-03 | Async job pattern so the UI never blocks | See section 7. | | MISSING |

---

## 14. Every PRD requirement ID

Verdict words: **Met**, **Partly met**, **Not met**. Evidence cites the rows above.

| ID | Requirement (short) | Verdict | Why (one line) |
|---|---|---|---|
| FR-ROUTE-1 | Do not guess intent | Partly met | Router refuses uncertain input, but it is not exposed through any route (A-01b). |
| FR-ROUTE-2 | Ask for clarification | Partly met | Same: logic exists and is tested, unreachable for users. |
| FR-ROUTE-3 | Sequence multi-capability requests | Partly met | `route_message` returns ordered actions; unreachable. |
| FR-INTEL-1 | Comprehensive scholarship fields | Partly met | Tables for every field exist; discovered records fill about eight columns. |
| FR-INTEL-2 | Value-status and confidence per field | Met | Present everywhere; for url-match the model self-declares it (T-07). |
| FR-INTEL-3 | Unknown never guessed | Met | Normalise and extraction prompts enforce it; rules only fill classified fields. |
| FR-INTEL-4 | Provenance and last-verified | Partly met | Provenance rows yes; `last_verified_at` means "seen again", not "verified" (V-01). |
| FR-INTEL-5 | Funding classification | Met | Seven-value enum, no binary flag. |
| FR-PROFILE-1 | Living profile | Met | GET/PUT/criteria/import. |
| FR-PROFILE-2 | Profile field set | Partly met | No age, spoken languages, finances, dependants, skills (section 6). |
| FR-PROFILE-3 | Hard/soft/exclusion plus missing info | Met | `profile_criteria.kind`, `missing_info`. |
| FR-PROFILE-4 | Matching treats kinds differently | Partly met | Hard and soft yes; exclusion has no effect (A-05e). |
| FR-DISC-1 | Official, broad-geography sources | Partly met | Five sources, five countries. |
| FR-DISC-2 | No random web search as primary | Met | Search is gated and unreachable. |
| FR-DISC-3 | Human-defined registry | Met | V-03. |
| FR-DISC-4 | Source discovery, extraction, normalise, dedup, verify, freshness, expired, new, coverage | Partly met | Dedup, verification and normalise are weak (S-02, S-04, V-01). |
| FR-DISC-5 | New sources are candidates | Met | V-03, V-05. |
| FR-DISC-6 | Systematic query dimensions | Partly met | Four of nine listed (V-07). |
| FR-DISC-7 | No 100% claim | Met | `Literal[False]`. |
| FR-DISC-8 | Operator-only approval | Not met | X-04. |
| FR-DISC-9 | National, regional and university sources in any country | Not met | Five sources, no jurisdiction model (feature 006 planned). |
| FR-MATCH-1 | Hard, soft, exclusions, unknown | Partly met | Exclusions have no effect. |
| FR-MATCH-2 | Explainable, not a percentage | Partly met | No percentage; `required_documents`/`remaining_actions` empty; soft evidence not validated. |
| FR-MATCH-3 | Hard failure differs from soft miss | Met | A-05, MA-04. |
| FR-MATCH-4 | Fixed verdict object | Met | `schemas/match.py`. |
| FR-MATCH-5 | Facts decided deterministically | Met | A-05a. |
| FR-MATCH-6 | Intelligent, criterion outcomes incl. Inferred | Partly met | `INFERRED` never produced (MA-07). |
| FR-MATCH-7 | Country/nationality names and aliases | Not met | MA-08. |
| FR-URL-1 | Match a URL directly | Met | Works; see X-05 for the risk. |
| FR-QA-1 | Ask about eligibility, funding, etc. | Partly met | Only for url-matched pages, and only while Qdrant is reachable (R-06, R-09). |
| FR-QA-2 | Grounded, structured, controlled | Partly met | Lexical grounding; numeric overstatement can pass (A-02c). |
| FR-QA-3 | Could-not-confirm instead of guessing | Met | Fixed sentence. |
| FR-DOC-1 | Document intelligence | Partly met | D-01. |
| FR-DOC-2 | Supported materials | Partly met | D-02. |
| FR-DOC-3 | Requirements derived dynamically | Partly met | Only url-matched scholarships have requirement rows. |
| FR-GEN-1 | Requirement-aware CV/SOP | Partly met | SOP questions yes; Europass is a label (W-03b). |
| FR-GEN-2 | Never fabricate | Met | D-03, with the requirement-fact caveat. |
| FR-PLAN-1 | Plan without other capabilities | Met | W-05. |
| FR-PLAN-2 | Six labels | Met | D-05. |
| FR-APP-1 | Guided journey | Partly met | The assistant reports but does not drive (A-04). |
| FR-APP-2 | Guided plus buttons, same result | Met | A-04b. |
| FR-APP-3 | Future submission safety rules | Met | Approval bound to scope and content; no send path. (Code also cites a "FR-APP-4"; the PRD defines FR-APP-1 to 3 only.) |
| FR-TRACK-1 | Track saved scholarships, applications, deadlines, status; readiness | Partly met | D-06. |
| FR-TRACK-2 | Interview tracking (future) | Not met | Deferred by the PRD. |
| FR-AUTH-1 | Sign up, log in, log out | Met | X-01. |
| FR-AUTH-2 | Per-user isolation | Met | X-03. |
| FR-VERIFY-1 | Governed pipeline philosophy | Partly met | Governance yes; verify step is weak. |
| FR-VERIFY-2 | LLM does not decide authority | Met | V-03. |
| FR-VERIFY-3 | Third parties are signals; official is final | Partly met | There is no step that checks the official page for discovered records. |
| FR-VERIFY-4 | Record carries source, type, official status, dates, status, evidence | Partly met | Evidence snippet never written (section 6). |
| FR-VERIFY-5 | Nine lifecycle states | Met | V-10. |
| FR-VERIFY-6 | Failed retrieval is not "none exists" | Met | V-11. |
| FR-VERIFY-7 | Conflict-resolution rule | Partly met | S-09: policy real, mostly dormant. |
| FR-VERIFY-8 | `verified` vs `listed` | Not met | Feature 004, not built. |
| FR-VERIFY-9 | Provider failures recorded apart | Not met | V-12. |
| FR-COV-1 | Measurable coverage | Partly met | S-07: no universities/providers. |
| FR-OUT-1 | Intent-specific schemas | Partly met | V-17. |
| FR-OUT-2 | Evidence, status, date, next action | Partly met | Q&A has source and date; no next action field. |
| FR-OUT-3 | Avoid unsupported claims, opaque scores | Partly met | No scores; unsupported claims possible via T-07 and A-02c. |
| FR-AGENTIC-1 | Genuine agentic AI where required | Partly met | The layering is right, but no component is an agent loop (section 1). |
| FR-AGENTIC-2 | No unnecessary sub-agents | Met | A-08. |
| FR-AGENTIC-3 | Fact-bearing agents guardrailed | Partly met | Matching yes; verification has no guardrailed agent. |
| NFR-1 | Explainability | Partly met | Matching explains; evidence not validated. |
| NFR-2 | Groundedness | Partly met | T-07, A-02c. |
| NFR-3 | Modularity | Met | Layered modules, repositories, tool tier. |
| NFR-4 | Buildable in units with handoffs | Met | Specs, tasks, ADRs, workstream artifacts exist. |
| NFR-5 | Dedicated entry points; long ops do not block UI | Not met | No UI; long routes are synchronous. |
| NFR-SEC-1 | AuthN, authZ, isolation | Partly met | No authorisation roles (X-04). |
| NFR-SEC-2 | Documents, secrets, validation, access control, logging | Partly met | X-05, X-06. |
| NFR-SEC-3 | One user's data never reaches another | Met | X-03. |
| NFR-REL-1 | Freshness first-class, configurable per source | Partly met | `freshness_window_days` exists; monitoring ignores `update_frequency`. |
| NFR-REL-2 | Failure and retry handling | Partly met | HTTP yes; ingestion no backoff; provider errors unhandled. |
| NFR-REL-3 | Monitoring with in-app and email alerts | Met | V-14, V-15. |
| NFR-REL-4 | Budgeted AI usage, "temporarily unavailable" message | Not met | No budget; quota errors are HTTP 500. |
| NFR-REL-5 | Cost-aware monitoring | Not met | V-13. |
| NFR-REL-6 | Accuracy not traded for cost | Met | Same model for everything; no skipped verification. |
| KPI-1 | Source coverage | Met | `GET /coverage`. |
| KPI-2 | Geographic and entity coverage | Partly met | Countries only. |
| KPI-3 | Extraction success rate | Not met | Not exposed. |
| KPI-4 | Verification rate | Not met | Not exposed. |
| KPI-5 | Freshness percentage | Not met | Not exposed. |
| KPI-6 | Duplicate rate | Not met | Not exposed. |
| KPI-7 | Matching accuracy, zero guardrail violations | Met | Matrix plus CI gate. |
| KPI-8 | Groundedness rate | Partly met | Five mocked fixtures. |
| KPI-9 | Failure and retry rate | Partly met | Prometheus counters and fetch log. |
| KPI-10 | AI quota use | Not met | No budget or counters. |
| KPI-11 | Verified share | Not met | Needs the `listed` status. |
| AC-1 | URL match plus "does it require GRE?" grounded answer | Partly met | Works in code and mocked tests; Q&A needs Qdrant, reported unreachable on 2026-10-22. |
| AC-2 | Discovery across 2+ source types, ranked, verified, with coverage | Partly met | Results are unranked and unverified; candidate flow works. |
| AC-3 | Upload transcript satisfies a requirement; grounded CV/SOP | Met | Tested end to end with mocks. |
| AC-4 | Labelled plan, assistant, button-agent parity | Partly met | Parity yes; assistant does not ask for or obtain approval per step. |
| AC-5 | Matching matrix passes, lock never overridden | Met | Ten-case matrix, 1.0 gate. |

**PRD tally:** see section 16.

---

## 15. Comparison with `docs/blueprint-gap-analysis.md`

**What the gap analysis got right and I confirm:** the unreachable orchestrator and `source_validate`; the stub search; the missing role gate on source approval; no frontend; `VERIFIED` meaning "seen twice"; the exact-string country filter; exclusive reliance on the LLM for monitoring; 003 as the first feature.

**What the gap analysis missed or got wrong**, each checked in the code above:

1. **SSRF on `POST /scholarships/match-url`.** It noted SSRF only for candidate validation (M-76). The user-facing URL fetch has no guard at all (X-05).
2. **Any user can trigger a monitoring run** (`POST /monitoring/runs`), which spends LLM quota. It flagged only source approval and `/metrics` (X-04).
3. **Exclusions are ignored by the verdict** (A-05e). It lists the matching agent as "Built, None" gap (M-42).
4. **A profile with no hard-constraint rows gets "Likely eligible"**, and a test locks that in (MA-03).
5. **Hard-constraint checks run only for criteria rows the user added**, not for the profile fields themselves (MA-01), and `float()` can crash on text values (S-10).
6. **`extract_requirements` output is not grounded and the LLM labels itself "verified"** (T-07). The analysis noted grounding of listing names only; it did not connect this to hard "Not eligible" verdicts.
7. **Discovery repeats results** once per plan step (V-07 / section 7) and returns every item of a visited source regardless of the profile.
8. **Dedup's `flag_for_review` is a dead end, and the key needs all three parts** (S-04). It marked dedup "Built". Conflict resolution is therefore mostly dormant (S-09), also marked "Built".
9. **Rerank is dead code** (T-10) and `rag/grounding.check_grounding` is dead code (R-07). It marked rerank "Built".
10. **Per-source rules are not enforced**: `robots_checked`, request delay, path prefixes, per-source user agent (T-02b). It recorded only that `robots_checked` is false.
11. **Every LLM route except documents returns HTTP 500 on a Gemini error** (section 7). It framed provider errors as a monitoring problem (M-23).
12. **Discovered scholarships store the source homepage as their official URL** (V-02), and are never indexed for Q&A (R-06). It noted `detail_url` is never followed, and indexing needing `raw_html`, but not the homepage URL.
13. **Qdrant chunks accumulate on every re-ingest** (R-05).
14. **Classification probably adds about one LLM call per ingested candidate**, not "~0-3 per day" (T-05). Marked *(by reading)*; worth measuring before 003.
15. **Ingestion retry has no backoff** (W-07).
16. **"Europass" is a heading word and the CV is a `.txt`** with **no download route** (W-03b, section 7). It marked CV generation "Built".
17. **CV grounding pool includes scholarship requirements** (D-03).
18. **The "agents" are not agent loops; no OpenAI Agents SDK, LangChain or MCP; no checkpointing or pauses** (sections 1, 2, 3). It counted "4 of 5 agents are real".
19. **`audit_log` table is missing; `timeout` fetch status is never written; `scholarship_fields.evidence_snippet` is never written** (section 6).
20. **`import_cv` blocks the event loop, has no size limit, and an invalid PDF is an unhandled error** (X-06).
21. **Evals gate is a fixed-threshold check, not a regression check, and the matching eval never runs soft criteria or exclusions** (E-04, E-07).
22. **GPA scale handled in `requirement_satisfaction` but ignored in `hard_constraints`** (S-13).
23. **Coverage has no universities/providers covered**; the analysis left this "not verified" (S-07).
24. **Repo monitoring interval default is still 60 minutes**; the Blueprint's "360 stopgap" is not visible in the repo (V-13).
25. **`url_match` spends an LLM call on a URL it already stored** (W-01b).

**Where I would soften its claims:** its counts (34 scholarships, 0 deadlines, 0 users) come from live queries I did not repeat, so I take them as given.

---

## 16. Count of each mark

Computed with `grep -o` on the tables in sections 1 to 13 (the Mark column). Row IDs with a letter suffix (for example A-02b) count as their own rows.

| Section | MATCHES | WEAKER | NAME ONLY | NOT REACHABLE | MISSING | Total |
|---|---|---|---|---|---|---|
| 1. Agents (Blueprint §7, §9, §21; PRD Part B) | 7 | 3 | 5 | 1 | 4 | 20 |
| 2. Workflows (Blueprint §10) | 4 | 7 | 1 | 1 | 2 | 15 |
| 3. Tools (Blueprint §11) and MCP (§15) | 2 | 8 | 1 | 2 | 2 | 15 |
| 4. Services (Blueprint §6 and elsewhere) | 5 | 6 | 2 | 0 | 0 | 13 |
| 5. RAG and vectors (Blueprint §12, §13) | 2 | 5 | 0 | 3 | 0 | 10 |
| 6. Database (Blueprint §14) | 9 | 3 | 0 | 1 | 1 | 14 |
| 7. API (Blueprint §5.1; OpenAPI contracts) | 3 | 9 | 0 | 0 | 4 | 16 |
| 8. Auth and security (Blueprint §19, §20; PRD NFR-SEC) | 3 | 5 | 0 | 0 | 3 | 11 |
| 9. Documents, CV/SOP, application planning and tracking (Blu | 3 | 4 | 0 | 0 | 1 | 8 |
| 10. Matching (Blueprint §18; PRD FR-MATCH) | 2 | 6 | 0 | 0 | 2 | 10 |
| 11. Source registry, discovery, pipeline, verification, fail | 5 | 8 | 1 | 0 | 3 | 17 |
| 12. Testing and evals (Blueprint §25, §25.1) | 2 | 5 | 0 | 0 | 2 | 9 |
| 13. Frontend (Blueprint §5) | 0 | 0 | 0 | 0 | 3 | 3 |
| **All sections 1-13** | **47** | **69** | **10** | **8** | **27** | **161** |

MATCHES 47 (29%); WEAKER 69 (43%); NAME ONLY 10 (6%); NOT REACHABLE 8 (5%); MISSING 27 (17%). Total rows marked: 161.

**PRD requirement IDs (section 14), 92 IDs:** Met 34, Partly met 43, Not met 15.

---

## 17. The 15 biggest problems, in simple words

1. **None of the "agents" is really an agent.** The system promises five AI agents that plan, use tools and loop. In the code, two make one AI call each, and the other three are ordinary functions. The OpenAI Agents SDK, LangChain and MCP are never used. This is a naming and promise gap, not a safety problem, but it changes what you can honestly say the product is.

2. **Matching can say "Likely eligible" while checking almost nothing.** It only checks the rules the user typed in as hard constraints, and only four kinds (GPA, nationality, degree, tests) plus the deadline. With no rules it still says "Likely". A text value like "3.0 or equivalent" can crash it.

3. **"I don't want this" rules do nothing.** If a student says "exclude anything that needs GRE", the program collects the answer and then ignores it when deciding the verdict.

4. **The word "Verified" is hollow.** No code ever opens a scholarship's own page to confirm it. A record becomes "verified" because the same list page showed it twice. Discovered records also carry the source's homepage as their "official URL" and no deadline.

5. **The AI marks its own homework on requirements.** When a user pastes a URL, the AI reads the page, writes down the requirements, and also labels each one "known" and "verified". Nothing checks that against the page. A wrong GPA or deadline can then produce a hard "Not eligible".

6. **Any signed-up user has operator powers, and the server can be turned against itself.** Anyone can approve new sources, start monitoring runs that spend the AI quota, and make the server fetch any web address, including internal ones on the VM, through the URL-match feature. Signup is open and there are no roles.

7. **The free AI allowance (about 20 calls a day) is unprotected.** There is no budget, every user action spends it, classification probably adds a call per new scholarship, and when Gemini says "quota used up" almost every endpoint returns a raw server error instead of "try again later".

8. **Q&A is fragile.** It cannot go and check the official page when it is unsure. It only works for scholarships added by URL (discovered ones are never indexed), it depends on Qdrant, which the repo's own notes say was unreachable on 2026-10-22, and its "Verified" label uses a check the code itself says can miss number errors.

9. **Discovery does not really use the profile.** The profile only picks which of the five sources to visit. The results come back unranked, not matched, and can repeat the same scholarship several times. "Germany" matches nothing because the filter expects "DE".

10. **Source rules are written down but not obeyed.** The registry stores robots status, delays, allowed paths and a bot name, and the governance notes say not to fetch until robots are checked. No code reads any of those, and two sources are fetched every hour.

11. **Workflows are straight pipes with no pause.** There is no saved progress, no resume, and no real "stop and wait for a human". The "approval gates" are a database lookup. The candidate-validation workflow and the orchestrator are built but nothing calls them.

12. **Duplicate and conflict handling is mostly asleep.** Duplicate detection only triggers when name, university and intake are all known, which listings never provide. A suspected duplicate is flagged and then ignored, so conflicts are rarely detected.

13. **The application helper stops short.** It tells you the next step but does not ask for documents or show drafts for approval. The "Europass CV" is a text file with the word Europass in its heading. Generated CVs and essays have no download route. Only two of about fifteen material types can be generated.

14. **No frontend, no background jobs.** There is no UI at all. Long operations (discovery, generation, URL match) block the request instead of running in the background.

15. **Tests give more comfort than they should.** 761 tests pass against mocked AI. The scored checks cover three narrow cases (10, 5 and 5 examples), compare to fixed thresholds instead of earlier scores, never test the exclusion path or real model output, and there are no extraction, verification or discovery evals and no checker-accuracy check. The RAG folder has zero tests.

---

## 18. Where this audit stopped, and what it did not cover

I finished all 14 areas. Not audited line by line: Blueprint §1-3 (vision and journeys), §22-§24 (phase plan and issue decomposition), §26-§28, §36-§46 (scope labels, process and revision notes), and the contents of `specs/001-*`, `specs/002-*`, `specs/003-*` task lists. `services/readiness.py`, `services/alerts.py` and `services/retention.py` were read at the level of their entry points and docstrings, not every branch. Items I could not judge from the repo are marked in the text (live data, VM `.env`, DB role privileges, whether tests currently pass).
