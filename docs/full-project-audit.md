# Full Project Audit: Blueprint v1.7 and PRD v1.1 against the real code

| | |
|---|---|
| Date | 2026-10-26 |
| Branch | `docs/full-project-audit` (from `main` @ `4574ad4`) |
| Compared against | `specs/scholarship-ai-assistant-master-blueprint-v1.md` (v1.7, "BP"), `specs/scholarship-ai-assistant-prd-v1.md` (v1.1, "PRD"), the Part B agent PRD inside the Blueprint, `specs/001-*/contracts/openapi.yaml`, `specs/002-*/contracts/openapi.yaml`, `docs/blueprint-gap-analysis.md` |
| Type | Read-only audit. No application code changed. No Gemini calls. No network calls. No database connection. Part 1 ran no tests; Part 2 ran test collection and the 571 tests that need neither database nor network (section P2.6); Part 3 ran no test suite, only a helper script that calls pure functions with the network blocked (P3.4). Part 4 ran only a helper script of pure functions and fakes (P4.3). |

## 0. How this was produced, and what to trust

**What I did.** I read the Blueprint and PRD in full, then opened the code for every item below. I judged each item from the file, never from its name or its docstring alone. Where a docstring claims something the code does not do, the code wins and I say so.

**The only things I executed** were two read-only Python snippets that import the app and print (a) the route list from `app.openapi()` and (b) the table and column list from the SQLAlchemy metadata. Neither connects to a database or to Gemini. I also diffed the live route list against both OpenAPI contract files.

**Not verified by running.** Live database contents (the gap analysis' counts of 34 scholarships, 0 deadlines, 0 users are taken from it, not re-checked). Test pass/fail status. The VM's real `.env`. Anything marked *(by reading)* is my reading of the code, not an observed run. Part 2 re-did every *(by reading)* item under mocks; see P2.5.

**Marks.**

| Mark | Meaning |
|---|---|
| MATCHES | The code does what the Blueprint or PRD says. |
| WEAKER | It exists and works, but does less than the document says. |
| NAME ONLY | It is named like the document's thing but is much simpler (for example a "workflow" that is a plain chain, an "agent" that is one function). |
| NOT REACHABLE | It is built, but no API route, scheduler or other live code path calls it. |
| MISSING | It is not built. |

For PRD requirement IDs (section 14) I use the three words you asked for: Met, Partly met, Not met.

**Short pointer to the end.** **Part 4 (every remaining file under `backend/app`, read line by line) is placed just before section 16.** The comparison with the gap analysis is section 15. **Part 2 (the areas Part 1 skipped) follows it, then Part 3 (the 92 PRD IDs re-judged, eight files read line by line, every wrong task box).** The count of each mark for all three parts is section 16, the 15 biggest problems are section 17, and where the audit stopped is section 18.

---

## 1. Agents (Blueprint §7, §9, §21; PRD Part B)

First, the headline fact: **none of the five "agents" is an agent loop.** There is no dependency on the OpenAI Agents SDK in `backend/pyproject.toml`. No file in `app/` imports `agents`, `openai`, `langchain`, or any MCP library (`grep` over `app/`: no `langchain` or `openai` import at all; the only `mcp` hits are an enum value and comments). `langchain` and `langchain-google-genai` are declared in `pyproject.toml` but never imported, so they are unused dependencies. The only LLM access is `app/core/llm.py:33` `generate_text` (one `generate_content` call, no tools, no loop, no retry, no budget) and `:48` `embed_texts`.

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
| W-01b | (Efficiency) | The LLM extraction runs **before** the "is this URL already stored?" check, so a repeat of a known URL still spends one LLM call and one Qdrant ingest. | `url_match/graph.py:68-73` (extract) comes before the lookup inside `_persist_scholarship_node` at `:76-81` *(by reading; confirmed by a mocked run in Part 2, BR-01)* | WEAKER |
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
| T-05 | `classify`: rules plus LLM for fuzzy | Rules use 32 country names and 20 fields and short keyword lists, substring match (`"uk"` in longer words). The ingestion call joins the candidate's raw values into one string (`"fully_funded"` has an underscore, so `\bfully[- ]funded\b` cannot match it). Any unresolved dimension triggers an LLM call, so **about one extra Gemini call per newly ingested candidate** *(by reading; Part 2 measured 1 call for each of three realistic candidates under mocks, BR-02; not measured on real pages)*. LLM failure is swallowed silently. | `app/tools/classify.py:55-62,122-147` (`except Exception:` at `:140`); `app/workflows/ingestion/graph.py:172-173` | WEAKER |
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
| S-10 | `hard_constraints`: GPA, degree, nationality, deadline, mandatory tests, plus country, field, funding, language, spouse per `profile_criteria` dimensions (§14 lists 10 dimensions) | Evaluators for **GPA, nationality, degree, test** plus a universal deadline check. The other dimensions (country, field, funding, language, spouse_dependent, other) always return `unknown`. GPA ignores `gpa_scale`. Nationality is an exact string match. `float(requirements[0].value)` and `float(required_score)` raise `ValueError` if the stored value is text like "3.0 or equivalent" *(reproduced under mocks in Part 2, BR-03)*, which surfaces as an HTTP 500 (the route has no `try`). | `app/services/hard_constraints.py:58,154,167-172,179-199` | WEAKER |
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
| R-05 | Re-ingesting a page replaces its old chunks (implied by "freshness") | Every upsert uses new random UUIDs (`str(uuid.uuid4())`) and never deletes. `url_match` re-ingests on every call, so chunks pile up and stale page text stays retrievable. | `qdrant_client.py:85`; `url_match/graph.py:116-123` *(by reading; reproduced under mocks in Part 2, BR-04: 4 distinct point ids after 2 ingests)* | WEAKER |
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
| Find Scholarships | `POST /discovery` (no body, uses the saved profile) | Output is a list of scholarship ids with source, type and status, plus coverage. It is **not ranked**, **not matched** to the profile, and the same source's items are appended once per plan step, so one source can appear several times *(reproduced under mocks in Part 2, BR-05: 16 rows for 4 distinct scholarships)*. The profile only chooses which sources to visit; the country filter is an exact match against seed codes like `DE`. Synchronous. | `app/agents/discovery/agent.py:141,300`; `app/api/discovery.py:37-60` | WEAKER |
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
| MA-03 | A profile with nothing evaluated must not look eligible | With no hard-constraint criteria and no soft criteria, the verdict is **"Likely eligible / Possible match"** only if the scholarship has a **known deadline that has not passed**. With no deadline it is "Unknown, requires verification" (Part 2 correction, BR-06). The "Likely" result is written into a test as the expected result. | `matching/agent.py:115-132`; `tests/services/test_hard_constraints_matrix.py:148-157` | WEAKER |
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

Counts from `grep "def test_"`: 761 test functions (functions, not tests: parametrization makes pytest collect **1,031** tests; see P2.6). By folder: services 173, api 112, workflows 102, sources 91, data 81, integration 54, tools 46, core 42, agents 19, infra 13, scheduling 12, smoke 5, **rag 0**.

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
| FR-GEN-1 | Requirement-aware CV/SOP | **Not met** | Part 3: Europass is a label; SOP questions are read from keys nothing in the app creates, so it works only for hand-inserted rows (SG-02). Was: Partly met. SOP questions yes; Europass is a label (W-03b). |
| FR-GEN-2 | Never fabricate | **Partly met** | Part 3: the grounding gate accepts a made-up grade because the scholarship's requirement text is in the allowed-facts pool (CG-01, CG-04). Was: Met. D-03, with the requirement-fact caveat. |
| FR-PLAN-1 | Plan without other capabilities | Met | W-05. |
| FR-PLAN-2 | Six labels | **Partly met** | Part 3: AI_CAN_GENERATE promised for four kinds nothing generates, the verified gate is passed by the AI's own label, due dates empty (RD-02, RD-05, RD-09, RD-10). Was: Met. D-05. |
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
| FR-VERIFY-5 | Nine lifecycle states | **Partly met** | Part 3: the monitor can never close or expire a stored record (SM-01, SM-02). Was: Met. V-10. |
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
| NFR-4 | Buildable in units with handoffs | **Partly met** | Part 3: hand-off artifacts do not exist (B-152 to B-154) and task boxes are unreliable (P3.3). Was: Met. Specs, tasks, ADRs, workstream artifacts exist. |
| NFR-5 | Dedicated entry points; long ops do not block UI | Not met | No UI; long routes are synchronous. |
| NFR-SEC-1 | AuthN, authZ, isolation | Partly met | No authorisation roles (X-04). |
| NFR-SEC-2 | Documents, secrets, validation, access control, logging | Partly met | X-05, X-06. |
| NFR-SEC-3 | One user's data never reaches another | Met | X-03. |
| NFR-REL-1 | Freshness first-class, configurable per source | Partly met | `freshness_window_days` exists; monitoring ignores `update_frequency`. |
| NFR-REL-2 | Failure and retry handling | Partly met | HTTP yes; ingestion no backoff; provider errors unhandled. |
| NFR-REL-3 | Monitoring with in-app and email alerts | **Partly met** | Part 3: no closed detection, no requirement-change detection, new-match alert needs a deadline, alerts beyond 500 dropped (SM-01, AL-07, AL-13). Was: Met. V-14, V-15. |
| NFR-REL-4 | Budgeted AI usage, "temporarily unavailable" message | Not met | No budget; quota errors are HTTP 500. |
| NFR-REL-5 | Cost-aware monitoring | Not met | V-13. |
| NFR-REL-6 | Accuracy not traded for cost | **Partly met** | Part 3: the alert re-match cap drops alerts permanently to save AI spend (AL-13). Was: Met. Same model for everything; no skipped verification. |
| KPI-1 | Source coverage | **Partly met** | Part 3: a never-fetched source counts as covered, so countries and the gap count are overstated (CO-01). Was: Met. `GET /coverage`. |
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
| AC-3 | Upload transcript satisfies a requirement; grounded CV/SOP | **Partly met** | Part 3: live satisfied path never shown (T130), one requirement per document (DP-03), and CV/SOP are not grounded only in real data (CG-01). Was: Met. Tested end to end with mocks. |
| AC-4 | Labelled plan, assistant, button-agent parity | Partly met | Parity yes; assistant does not ask for or obtain approval per step. |
| AC-5 | Matching matrix passes, lock never overridden | Met | Ten-case matrix, 1.0 gate. |

**PRD tally after Part 3:** Met 26, Partly met 50, Not met 16 (before: 34 / 43 / 15). The nine rows that changed are marked "Part 3" and listed in P3.1. Section 16 has the totals.

---

## 15. Comparison with `docs/blueprint-gap-analysis.md`

**What the gap analysis got right and I confirm:** the unreachable orchestrator and `source_validate`; the stub search; the missing role gate on source approval; no frontend; `VERIFIED` meaning "seen twice"; the exact-string country filter; exclusive reliance on the LLM for monitoring; 003 as the first feature.

**What the gap analysis missed or got wrong**, each checked in the code above:

1. **SSRF on `POST /scholarships/match-url`.** It noted SSRF only for candidate validation (M-76). The user-facing URL fetch has no guard at all (X-05).
2. **Any user can trigger a monitoring run** (`POST /monitoring/runs`), which spends LLM quota. It flagged only source approval and `/metrics` (X-04).
3. **Exclusions are ignored by the verdict** (A-05e). It lists the matching agent as "Built, None" gap (M-42).
4. **A profile with no hard-constraint rows gets "Likely eligible"** when the scholarship has a known future deadline (Part 2 narrowed this: with no deadline it is "Unknown"), and a test locks that in (MA-03).
5. **Hard-constraint checks run only for criteria rows the user added**, not for the profile fields themselves (MA-01), and `float()` can crash on text values (S-10).
6. **`extract_requirements` output is not grounded and the LLM labels itself "verified"** (T-07). The analysis noted grounding of listing names only; it did not connect this to hard "Not eligible" verdicts.
7. **Discovery repeats results** once per plan step (V-07 / section 7) and returns every item of a visited source regardless of the profile.
8. **Dedup's `flag_for_review` is a dead end, and the key needs all three parts** (S-04). It marked dedup "Built". Conflict resolution is therefore mostly dormant (S-09), also marked "Built".
9. **Rerank is dead code** (T-10) and `rag/grounding.check_grounding` is dead code (R-07). It marked rerank "Built".
10. **Per-source rules are not enforced**: `robots_checked`, request delay, path prefixes, per-source user agent (T-02b). It recorded only that `robots_checked` is false.
11. **Every LLM route except documents returns HTTP 500 on a Gemini error** (section 7). It framed provider errors as a monitoring problem (M-23).
12. **Discovered scholarships store the source homepage as their official URL** (V-02), and are never indexed for Q&A (R-06). It noted `detail_url` is never followed, and indexing needing `raw_html`, but not the homepage URL.
13. **Qdrant chunks accumulate on every re-ingest** (R-05).
14. **Classification probably adds about one LLM call per ingested candidate**, not "~0-3 per day" (T-05). Marked *(by reading)* in Part 1; Part 2 measured it under mocks (1 call per candidate, BR-02); still worth measuring on real pages before 003.
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

## Part 2: the areas Part 1 skipped

Part 2 was done on 2026-10-26 on the same branch. It is still read-only: no application code changed, no real Gemini call, no network call, no database connection. Two helper scripts were added under `docs/audit-part2-scripts/` (they are not application code; each one is described where it is used).

**Methods.** Each row says how it was checked.

| Letter | Method |
|---|---|
| **R** | Read the code and traced the exact call path from the route or scheduler to the line. |
| **M** | Ran the real code with the LLM, HTTP, Qdrant and database mocked, with every socket connection made to raise (`docs/audit-part2-scripts/mock_run.py`). |
| **S** | Symbol scan: the file exists and the names the task or promise mentions are in it. This proves the thing was written, not that it behaves as claimed. |
| **T** | Ran a subset of the test suite that needs neither the database nor the network (section P2.6). |
| **U** | Cannot be checked from the repo. Section P2.7 gives you a read-only command. |

**Marks.** Same five marks as Part 1 (MATCHES, WEAKER, NAME ONLY, NOT REACHABLE, MISSING). Where something cannot be judged from the repo I use **UNCHECKED** and count it separately. Task lists (P2.3) use their own words (Done, Partly, Not done), because a task is either built or not.

**What changed my mind about Part 1.** Three Part 1 statements were wrong or imprecise and are corrected in P2.8: "761 tests pass", "a profile with no rules gets Likely eligible" (it also needs a known future deadline), and the word "expect" on the classify cost (now measured under mocks).

---

### P2.1 Blueprint §1-3: every user journey, step by step

#### Journey 3.1, Discovery ("find scholarships that match me")

| ID | Step in the Blueprint | What actually runs it (route / code), or that nothing does | Method | Mark |
|---|---|---|---|---|
| J-01 | User sends a natural-language request plus profile | `POST /discovery` takes **no body**. It loads the saved profile and returns 422 if nationality, degree, fields and countries are all empty. A natural-language request is not accepted anywhere. `app/api/discovery.py:37-60` | R | WEAKER |
| J-02 | React UI sends it | No frontend exists. Routes are reachable only by raw HTTP. `ls` of the repo shows no `frontend/` | R | MISSING |
| J-03 | FastAPI → Main agent → hands off → Discovery agent | The route calls `run_discovery` directly (`api/discovery.py:37-60`). `handle_message` (the Main agent) has no caller outside tests (`orchestration/main_agent.py:231`, `grep handle_message app/`). There is no handoff. | R | NOT REACHABLE |
| J-04 | Discovery reads the governed Source Registry | `build_query_plan` reads only `source_repo.get_active_sources` (active, `discovery_role=true`). `app/agents/discovery/agent.py:117-154` | R | MATCHES |
| J-05 | Builds a multi-strategy query plan | A grid of (dimension value, source) for country, field, degree level, nationality. Country is filtered by source; the other three add **every** discovery source once per value. In the mocked run a 4-value profile and 2 sources made 8 plan steps (P2.5, BR-05). `agent.py:130-154` | M | WEAKER |
| J-06a | Channel: approved scholarship APIs | `api_connector_tool` runs only for `access_method=api`. No seeded source uses it, and the transport is a placeholder. `agent.py:269-282`, `sources/connectors/api_connector.py:58` | R | NAME ONLY |
| J-06b | Channel: official sources (gov, university, provider) | `listing_fetch_tool` runs for `web` sources. The seed has 5 sources, only 2 have `discovery_role: true` (EACEA, HEC). DAAD, Stipendium Hungaricum and KAUST are `false`. `agent.py:196-232`; `app/data/seeds/seed_sources.yaml:20,50,78,108,136` | R | WEAKER |
| J-06c | Channel: approved search tools | Nothing in `app/` calls `search_tool` (see T-04). | R | NOT REACHABLE |
| J-06d | Channel: approved aggregators (discovery signal only) | No aggregator is seeded; `approved_aggregator` is only an enum value. Seed types: national_education, international_org, gov, gov, university. | R | MISSING |
| J-07 | Candidate pool and `source_fetch_log` (ok / fail / retry) | The connector writes a log row for every fetch and extraction, success or failure; failures are also kept in `fetch_errors`. `sources/connectors/official_fetch.py`; `agent.py:237-247` | R | MATCHES |
| J-08 | Pipeline EXTRACT → NORMALIZE → CLASSIFY → DEDUP → VERIFY → STORE → INDEX → MATCH | Extract is a tool call; normalize only wraps values; classify makes one LLM call per candidate (BR-02); dedup rarely matches (S-04); verify only computes a label (V-01); store works; **index never runs** for discovered records (needs `raw_html`); **there is no match step** (W-07, W-07b). | R | WEAKER |
| J-09 | Store in PostgreSQL and Qdrant | PostgreSQL yes. Qdrant no, for every discovered record (R-06). | R | WEAKER |
| J-10 | Matching service and deterministic ranking over the results | `POST /discovery` neither matches nor ranks. The caller must call `POST /scholarships/{id}/match` once per id. Ranking exists only inside `GET /matches` (`app/api/matching.py:56`) and only over matches already made. | R | MISSING |
| J-11 | Results plus coverage summary | `DiscoveryResult(results, coverage)` always carries coverage (`agent.py:337`). Results repeat: the mocked run returned **16 rows for 4 distinct scholarships** (BR-05). | M | WEAKER |
| J-12 | UI shows it to the user | No UI. | R | MISSING |

#### Journey 3.2, URL matching

| ID | Step in the Blueprint | What actually runs it | Method | Mark |
|---|---|---|---|---|
| J-20 | User provides a URL | `POST /scholarships/match-url`, body `{url: HttpUrl}`. `app/api/url_match.py:20-39` | R | MATCHES |
| J-21 | Node `fetch` (MCP / web tool) | `fetch_url`: plain `httpx.get`, redirects followed, no size limit, no private-address check. `workflows/url_match/graph.py:61-66`; `tools/web_fetch.py:26-33` | R | WEAKER |
| J-22 | Node `extract` (LLM structured output → requirements JSON) | `extract_requirements_from_page`: one LLM call, no retry on invalid JSON, text cut at 20,000 characters. `graph.py:68-73` | R | WEAKER |
| J-23 | Node `normalize` → canonical Requirement model | There is no normalize node. Extracted values are copied straight into `Requirement` rows. `graph.py:76-111` | R | MISSING |
| J-24 | Node `load profile` | The route loads it (`get_or_create_profile`, `url_match.py:30`) and passes it in. | R | MATCHES |
| J-25 | Node `match` (verdict + criteria breakdown) | Hard constraints, then the matching agent, then `build_verdict`. Real, but see MA-01 to MA-03. `graph.py:126-141` | R | WEAKER |
| J-26 | Node `gaps` (missing info / missing docs) | `missing_information` is the profile's own six global gaps. **`required_documents` and `remaining_actions` are hard-coded to empty lists for every verdict.** `agents/matching/agent.py:165-166` (the only writer; `grep` shows no other) | R | MISSING |
| J-27 | Node `attribute` (source URL + retrieval date) | The result carries `official_source` (the pasted URL). There is no retrieval date in `UrlMatchResult` (`schemas/match.py:88-91`) and `last_verified_at` is not set. | R | WEAKER |
| J-28 | Explainable result (verdict, matched / failed / unknown, docs, actions) | Verdict, matched, failed and unknown lists are present; docs and actions are always empty (J-26). | R | WEAKER |

#### Journey 3.3, Application preparation

| ID | Step in the Blueprint | What actually runs it | Method | Mark |
|---|---|---|---|---|
| J-30 | Scholarship (matched) | `POST /applications` accepts any existing scholarship id. It does not require a match. `app/api/application.py:41-52` | R | WEAKER |
| J-31 | Requirements → eligibility | `app_plan` loads the stored requirements. Eligibility is not re-evaluated in the plan. `workflows/app_plan/graph.py:112-123` | R | WEAKER |
| J-32 | Application planner → tasks and checklist | `POST /applications/{id}/plan`. Deterministic, idempotent. | R | MATCHES |
| J-33 | Required documents identified | One checklist item per stored requirement. A requirement key that is not exactly a `DocumentType` value (for example "statement of purpose") falls back to the category default or to "needs human review". `services/readiness.py:122-131,186` | R | WEAKER |
| J-34 | User uploads documents → document workflow (parse / associate / check) | `POST /applications/{id}/documents`. PDF only; the declared type is trusted (W-02). | R | WEAKER |
| J-35 | Missing items flagged (for example character certificate) | Labels `user_must_obtain` / `missing` exist. A character certificate appears only if the scholarship has a requirement keyed `character_certificate`. **Task due dates are never filled**: `ChecklistItemDraft.due_date` is never passed (`readiness.py:272-280`). | R | WEAKER |
| J-36 | CV generation workflow | `POST /applications/{id}/generate/cv`. Output is a `.txt` file; no download route (W-03, W-03b). | R | WEAKER |
| J-37 | SOP / motivation workflow | `POST /applications/{id}/generate/sop`. Real and grounded (W-04); no download route. | R | WEAKER (Part 3: SG-01, SG-02, SG-04; was MATCHES) |
| J-38 | Other written materials | No route or workflow exists for personal statement, study plan or research proposal. `GeneratedDocumentType.MOTIVATION` is never written by any code (only read in `readiness.py:116`). Yet the readiness table labels all of these `ai_can_generate` (`readiness.py:77-85,173-176`), a label that promises something the system cannot do. | R | MISSING |
| J-39 | Case compiled → readiness assessment | `POST /applications/{id}/assistant/next-step` runs `app_plan` then `submit_prep`; the "case" is a staged list of document ids (W-06). | R | WEAKER |
| J-40 | Review → [future] user approval → submit | Approval: `POST /applications/{id}/submission-approvals` (append-only). Submit: no code path, enforced by a static test (`tests/api/test_no_live_submission.py`). | R | MATCHES |

#### The narrative sentences in §3 and the §1 promises

| ID | Sentence | What runs it | Method | Mark |
|---|---|---|---|---|
| J-50 | "Here is my profile, find scholarships that match me" → results | `POST /discovery` returns ids with a verification label, not matches (J-10, J-11). | R | WEAKER |
| J-51 | "Tell me everything about this one" → researched | `GET /scholarships/{id}` returns the stored row. No research step exists (A-02b). | R | MISSING |
| J-52 | "Does it allow my spouse? Does it require GRE?" → grounded from official sources | `POST /scholarships/{id}/qa`: grounded over page chunks, only for scholarships added by URL; no live check (A-02, R-06). | R | WEAKER |
| J-53 | "Match my profile against it" → verdict and gaps | `POST /scholarships/{id}/match` works; gaps are generic (J-26). | R | WEAKER |
| J-54 | "Create the application plan / what documents do you need?" → checklist | `POST /applications/{id}/plan`. | R | MATCHES |
| J-55 | §1: "every important fact is verified against the official source with preserved provenance" | Not done: V-01, V-02. | R | MISSING |
| J-56 | §1: "never claims no scholarship missed; exposes coverage and gaps" | `claims_complete_coverage` is typed `Literal[False]`; coverage and gaps are always returned. `schemas/discovery.py:29-48` | R | MATCHES |
| J-57 | §1: BS, MS and PhD from day one | `DegreeLevel` has BS, MS, PhD. | R | MATCHES |
| J-58 | §2 table, "Source monitoring: **Excluded** (design only)" in the MVP column | Monitoring is built and deployed (Phase 5). The table was never updated. | R | WEAKER |
| J-59 | §2 table, "Q&A: grounded, RAG + **live fetch**" | No live fetch (A-02b). | R | MISSING |
| J-60 | §2 table, Auth "Production: refresh tokens, password reset, rate limits" | Rate limits exist (`core/rate_limit.py`). No refresh token, no password reset (`grep` over `app/` finds neither). Logout is a no-op (`api/auth.py:49-54`). | R | WEAKER |

---

### P2.2 Blueprint §22-24, §26-28, §36-46: every promise, scope label and "done" claim

How to read the marks here: a row marked MATCHES means the code agrees with what the sentence says. A capability that the Blueprint itself says is future, and that is correctly absent, is MATCHES (the claim is "not yet"). A "✅" the Blueprint gives itself is checked as a claim.

#### §22 Phase / tier plan

| ID | Promise or "done" claim | What the code shows | Proof | Method | Mark |
|---|---|---|---|---|---|
| B-01 | Phase 0 DoD: sign up / log in / log out; `docker-compose up` runs the stack; CI green with the eval gate active | Auth routes, `docker-compose.yml`, and a CI step `python -m evals.runner` all exist. I did not start the stack. Logout is a no-op. | `api/auth.py:49-54`; `.github/workflows/ci.yml:75-77` | R | MATCHES |
| B-02 | Phase 1 scope: `url_match` fetch → extract → normalize → match → gaps → attribute | No normalize node, gaps are generic, no retrieval date (J-23, J-26, J-27) | see P2.1 | R | WEAKER |
| B-03 | Phase 1 scope: Research/Q&A agent + RAG + **MCP fetch** | RAG yes; no MCP; no live fetch | `agents/research_qa/agent.py:28-54` | R | NAME ONLY |
| B-04 | Phase 1 scope: Qdrant ingestion for fetched pages | Done by `url_match` (but chunks pile up, R-05, confirmed BR-04) | `workflows/url_match/graph.py:116-123` | M | MATCHES |
| B-05 | Phase 1 tests: extraction schema tests, RAG retrieval test, agent tool-call tests | `tests/rag/` has only `__init__.py`; no tool-call tests (agents make no tool calls) | `tests/rag/` | R | WEAKER |
| B-06 | Phase 1 evals: matching, Q&A **and checker-accuracy check** | Matching and Q&A only. No checker-accuracy check (E-06). | `evals/runner.py:50,70-77` | R | MISSING |
| B-07 | Phase 1 DoD: paste a real URL → explainable verdict **and gaps** | Verdict yes; gaps are the profile's six global gaps, documents and actions empty | `agents/matching/agent.py:165-166` | R | WEAKER |
| B-08 | Phase 1 DoD: "does it require GRE?" → grounded answer with source URL + label | Works when Qdrant and Gemini are up and the scholarship was added by URL | `api/qa.py:18`; `agents/research_qa/agent.py` | R | MATCHES |
| B-09 | Phase 2 scope: Source Registry + seed + candidate flagging with **manual approval** | Registry, 5 seeds, candidate table and approve/reject routes exist; any signed-in user can approve (X-04) | `app/api/sources.py:59,82` | R | WEAKER |
| B-10 | Phase 2 scope: multi-source Discovery using `api_connector`, `official_fetch`, `search` | `official_fetch` real; `api_connector` placeholder; `search` raises `NotImplementedError` and has no caller | `tools/search.py:40`; `api_connector.py:58` | R | WEAKER |
| B-11 | Phase 2 scope: pipeline "extract → normalize → classify → dedup → **verify(official)** → store → index → **match**" | verify is a label; index and match do not run for discovered records (J-08) | `workflows/ingestion/graph.py:641-673` | R | WEAKER |
| B-12 | Phase 2 scope: "ranking; **semantic search (Qdrant)**" | Qdrant holds only page chunks for Q&A. Nothing searches scholarships semantically. Ranking applies only in `GET /matches`. | `data/vectors/qdrant_client.py`; `api/matching.py:56` | R | MISSING |
| B-13 | Phase 2 DoD: discovery returns **ranked, verified, source-attributed** results plus coverage; new source lands as a candidate | Source-attributed yes; coverage yes; candidate yes (ADR-0005). Not ranked, not verified (J-10, V-01). | `api/discovery.py:37-60` | R | WEAKER |
| B-14 | Phase 2 "leaving the laptop": minimal cloud runtime; provider "⚠️ NOT YET DEFINED" | Built (Oracle VM, Podman, Caddy with `tls internal`). The Blueprint text was never updated to name it. Whether it is running now: unchecked. | `docker-compose.podman.yml`; `Caddyfile`; `docs/vm-deployment-notes.md` | R, U | WEAKER |
| B-15 | Phase 3 DoD: upload transcript → associated and satisfies a requirement; CV/SOP grounded in real data | Both paths exist and are tested with mocks. The task list itself says the live SATISFIED path "has never been demonstrated" (T130). | `specs/001-*/tasks.md` T130; `workflows/doc_pipeline/graph.py` | R | WEAKER |
| B-16 | Phase 4 scope: Application/Submission **Agent** with per-step detail requests and approval gates | One rule table over the checklist; no LLM; cannot request a document or present a draft (A-04) | `agents/application/agent.py:1-5,63-108` | R | NAME ONLY |
| B-17 | Phase 4 DoD: "prepare my application" → agent walks steps, requests missing docs, gets approvals, outputs a ready case | Not met: it reports the next step. It never asks for a file, generates a draft or pauses for approval except at final submit. | `agents/application/agent.py:63-108` | R | MISSING |
| B-18 | Phase 4 tests: approval-gate enforcement; workflow parity (button vs agent) | Both exist (`test_submission_approval_scope.py`, parity by shared functions) | `tests/services/test_submission_approval_scope.py` | S | MATCHES |
| B-19 | Phase 5 scope: observability, rate limits, retries/backoff, security hardening, backups | JSON logs + Prometheus yes; per-IP rate limit yes (works behind Caddy on the VM); HTTP backoff yes but **none for ingestion**; security: open signup, no SSRF guard on URL match; backups are Neon's (not in the repo) | `core/logging.py`; `core/rate_limit.py`; `workflows/ingestion/graph.py:126`; Neon: U | R, U | WEAKER |
| B-20 | Phase 5 scope: `source_monitor`, source health, semi-automated candidate validation | All three built and reachable | `workflows/source_monitor/graph.py:542-629`; `api/health.py`; `services/candidate_validation.py` | R | MATCHES |
| B-21 | Phase 5 scope: "coverage analytics (dashboards over `source_fetch_log`)" | One JSON endpoint `GET /coverage`; no dashboard | `api/coverage.py` | R | WEAKER |
| B-22 | Phase 5 DoD: approved sources re-checked on a schedule; new/changed/closed picked up; stale flagged | Yes, with the caveats of V-05 to V-13 | `scheduling/scheduler.py:53-77`; `source_monitor/graph.py` | R | WEAKER (Part 3: SM-01, closed is never detected; was MATCHES) |
| B-23 | Phase 5 status: "deployed to the VM with HTTPS 2026-10-19; post-deploy checks passed; interval raised to **360** on 2026-10-25" | Repo default is still 60 (`config.py:48`). Deployment, checks and the VM value cannot be seen from the repo. | `app/core/config.py:48` | R, U | UNCHECKED |
| B-24 | Phase 6 (SaaS, live submission) not built | Correct: no tenancy, billing, roles or submit path | repo-wide; `tests/api/test_no_live_submission.py` | R | MATCHES |
| B-25 | Roadmap row 003: "spec, plan, tasks, ADR-0014 done; **not implemented**" | Correct. 76 of 76 tasks unchecked and none of its files or symbols exist (P2.3) | `specs/003-quota-governance/tasks.md`; no `llm_gate.py`, `llm_budget`, `cadence` in `app/` | R | MATCHES |
| B-26 | Roadmap rows 004-009 "not started" | Correct: no spec folder and no code for them | `ls specs/` | R | MATCHES |

#### §23-24 Spec-driven model and issue decomposition

| ID | Promise | What the code / repo shows | Proof | Method | Mark |
|---|---|---|---|---|---|
| B-30 | §23 layout `specs/phase-N/`, `specs/contracts/`, `specs/adr/` | Real layout is `specs/00N-*/` with `contracts/` inside each, and `adr/` at the repo root | `ls specs adr` | R | WEAKER |
| B-31 | §23 "each acceptance criterion traces to a requirement id" | 002 tasks carry `Satisfies: FR-…`; 001 tasks mostly do not | `specs/002-*/tasks.md`; `specs/001-*/tasks.md` | R | WEAKER |
| B-32 | §23 "a phase is done only when its acceptance table is fully green" | 001 still has 71 unticked tasks and three scenarios its own T130 calls open, while the MVP is treated as done | `specs/001-*/tasks.md:263` | R | WEAKER |
| B-33 | §24 Issue 1: `extract_requirements` — "invalid JSON triggers **one retry** then error" | No retry; invalid JSON raises | `tools/extract_requirements.py:88-94` | R | MISSING |
| B-34 | §24 Issue 2: matching "LLM tool only for fuzzy, labelled `inferred`; never a bare percentage; **missing docs listed**" | First two hold. Missing docs are never listed (`required_documents=[]`). | `agents/matching/agent.py:100-107,165` | R | WEAKER |
| B-35 | §24 Issue 3: Q&A "retrieve → grounding-check → if weak, **MCP fetch**" | No escalation (A-02b) | `agents/research_qa/agent.py:52-54` | R | MISSING |
| B-36 | §24 Issue 4: registry; connectors reject non-`active`; pending candidates never authoritative | Holds, tested. The named file `sources/registry.py` does not exist (logic is in `services/source_registry.py` and `source_repo.py`). | `sources/connectors/official_fetch.py:139-150`; `tests/sources/test_source_registry_compliance.py` | R | MATCHES |
| B-37 | §24 Issue 5: ingestion writes provenance, logs every fetch, unverifiable facts stored `unverified`, conflicts by policy | Logging and `unverified` hold; conflict policy runs rarely (S-04, S-09) | `workflows/ingestion/graph.py` | R | WEAKER |
| B-38 | §24 "GitHub issue decomposition", every issue cites its spec section | The repo has `tasks.md` per feature instead; no issue list is in the repo, so GitHub itself is unchecked | `specs/*/tasks.md` | U | UNCHECKED |

#### §26-27 Evolution, assumptions A1-A18

| ID | Claim | What the code shows | Proof | Method | Mark |
|---|---|---|---|---|---|
| B-40 | §26 Production list: observability, retries/backoff, **caching**, rate limits, backups, hardening, deployment | All except answer caching (only a listing-page TTL cache exists) | `official_fetch.py:235` | R | WEAKER |
| B-41 | §26 SaaS items kept out of the MVP | Correct | repo-wide | R | MATCHES |
| B-42 | A1 Discovery and Research stay separate agents | Separate modules, both are plain functions | `agents/discovery/`, `agents/research_qa/` | R | NAME ONLY |
| B-43 | A2 Embeddings default to a local HF model | Gemini `gemini-embedding-001`; flagged in §46 as undecided | `core/llm.py:12` | R | WEAKER |
| B-44 | A3 Reranker off by default | Off, and nothing calls it | `tools/rerank.py:40` | R | NOT REACHABLE |
| B-45 | A4 Component library chosen in Phase 0 | No frontend, no choice recorded | repo-wide | R | MISSING |
| B-46 | A5 MCP = fetch + search servers, providers chosen in Phase 1 | No MCP anywhere | `models/source.py:47` enum only | R | MISSING |
| B-47 | A6 Local-FS storage with an S3-compatible interface later | `StorageBackend` Protocol plus local FS | `data/files/storage.py` | R | MATCHES |
| B-48 | A7 Submit-and-poll for long calls | Only `POST /monitoring/runs` (202 + `GET /monitoring/runs/{id}`). Discovery, generation, URL match, document upload all block. | `api/monitoring.py:67-80` | R | WEAKER |
| B-49 | A8 One agent orchestrates shared workflows; same workflows as buttons | Shared workflows yes (parity holds); the agent does not orchestrate (A-04) | `agents/application/agent.py` | R | WEAKER |
| B-50 | A9 Discovery stays one agent; no per-source agents | Holds | repo-wide | R | MATCHES |
| B-51 | A10 Source approval human-governed; LLM never self-authorises a source | Human gate and "LLM cannot approve" hold; but any user, not an operator, can approve (X-04) | `api/sources.py:59,82` | R | WEAKER |
| B-52 | A11 Small seed registry in the MVP | 5 sources | `seed_sources.yaml` | R | MATCHES |
| B-53 | A12 Coverage measured, never asserted complete | `claims_complete_coverage: Literal[False]` | `schemas/discovery.py:29-48` | R | MATCHES |
| B-54 | A13 Hard constraints **and exclusions** computed deterministically, passed to the agent as locked | Hard constraints yes. Exclusions are produced by the LLM and then ignored by the verdict (A-05e, BR-08) | `agents/matching/agent.py:111-132` | M | WEAKER |
| B-55 | A14 Stay on the free tier; "accuracy never traded for cost: no lighter model, no skipped verification" | The model is `gemini-2.5-flash`; no budget gate exists (003 unbuilt); no verification is skipped because none exists | `core/llm.py:11` | R | WEAKER |
| B-56 | A15 "verified" = confirmed on the official page with deadline and eligibility; weaker status `listed` | `VerificationStatus` has only `verified / unverified / conflicting`. The new meaning is a decision for feature 004. | `models/scholarship.py:43-46` | R | MISSING |
| B-57 | A16 Every LLM call passes one central budget; students get "temporarily unavailable", never a server error | No budget, no gate; Gemini errors surface as HTTP 500 on all LLM routes except documents (section 7) | `core/llm.py:33`; `specs/003-*/tasks.md` | R | MISSING |
| B-58 | A17 Source approval restricted to operators | No role column, no check | `models/user.py` | R | MISSING |
| B-59 | A18 Large-scale expansion scheduled as 006 | Matches the roadmap; nothing built | roadmap | R | MATCHES |

#### §28 Requirements Coverage Audit: the Blueprint's own 31 "✅"

| ID | The Blueprint's claim (all ✅) | Verdict | Why | Method | Mark |
|---|---|---|---|---|---|
| B-60 | Every major requirement covered | PRD tally in section 14: 34 met, 43 partly, 15 not | `docs/full-project-audit.md` §14 | R | WEAKER |
| B-61 | No user workflow missing (§3, §5) | 9 of the 35 journey steps in P2.1 have no code or no reachable code (7 MISSING, 2 NOT REACHABLE); no frontend | P2.1 | R | WEAKER |
| B-62 | No unnecessary agents (5, each justified) | The five are not agent loops | §1 | R | NAME ONLY |
| B-63 | No unjustified technology | `langchain` and `langchain-google-genai` are declared and never imported | `backend/pyproject.toml:14-15` | R | WEAKER |
| B-64 | React + TS, no Streamlit | No frontend of any kind | repo | R | MISSING |
| B-65 | FastAPI modular monolith | Yes | `app/main.py` | R | MATCHES |
| B-66 | Qdrant / Postgres / files boundaries explicit | Yes, with the chunk pile-up (R-05) | `data/vectors/`, `data/files/` | R | MATCHES |
| B-67 | RAG proper; MCP justified | RAG partial; MCP absent | §3, §5 | R | WEAKER |
| B-68 | Auth + authorization + user isolation | Isolation yes (repos are user-scoped, tests exist for applications, documents, alerts, approvals); no roles | `data/repositories/profile_repo.py:39-130` | R | WEAKER |
| B-69 | BS / MS / PhD supported | Yes | `models/scholarship.py:12-16` | R | MATCHES |
| B-70 | Discovery, URL match, profile match, Q&A, **official research** | Official research does not exist | J-51 | R | WEAKER |
| B-71 | Documents + CV/SOP grounded (no fabrication) | Gate is lexical (ADR-0003) and is real for CV/SOP; output is `.txt` | `tools/ground_check.py`; `workflows/cv_gen/graph.py:226-337` | R | WEAKER |
| B-72 | Planning + preparation + tracker | Yes | `api/application.py` | R | MATCHES |
| B-73 | Application/Submission agent (step by step, asks details, approval-gated) | Does not ask for details | A-04 | R | NAME ONLY |
| B-74 | Dual mode, no duplicated logic | Both call the same two workflow functions | `agents/application/agent.py:63,85`; `api/application.py:89-100` | R | MATCHES |
| B-75 | Future live submission with mandatory approval | No send path; approvals append-only and scope-bound | `services/submission_approval.py` | R | MATCHES |
| B-76 | Phase independence; specs as artifacts | Specs exist; the layout differs (B-30) | B-30 | R | WEAKER |
| B-77 | GitHub issue decomposition + Phase-1 examples | See B-33 to B-38 | B-38 | R | WEAKER |
| B-78 | MVP → Production → SaaS as separate future work | Yes | §26 | R | MATCHES |
| B-79 | Anti-hallucination (Verified/Inferred/Unknown) + conflict policy | Q&A check is lexical and misses number errors; extraction labels itself "verified"; conflict policy mostly dormant | A-02c, T-07, S-09 | R | WEAKER |
| B-80 | Agentic skill without artificial complexity | There is almost no agentic skill | §1 | R | NAME ONLY |
| B-81 | Governed multi-source discovery, no single-provider reliance | 5 sources, 2 monitored | `seed_sources.yaml` | R | WEAKER |
| B-82 | Source Registry + controlled expansion (human-approved) | Approval open to every user | X-04 | R | WEAKER |
| B-83 | Official source is final authority; provenance preserved | No code opens the official page (V-01); `evidence_snippet` never written | `data/repositories/scholarship_repo.py:91-106` | R | NAME ONLY |
| B-84 | Measurable coverage, no completeness claim | Yes | B-53 | R | MATCHES |
| B-85 | Failure/retry: a failed fetch is not "none found" | Holds for fetches (log rows, `fetch_errors`) | `official_fetch.py`; `agent.py:237-247` | R | MATCHES |
| B-86 | Data-quality pipeline (classify + dedup added) | Both exist and are weak | S-04, T-05 | R | WEAKER |
| B-87 | Structured per-intent response schemas | §34 lists five intents. Only "is this a match" has its own schema. Spouse, funding and "how do I apply" all use one `QaAnswer` with a free-text `answer_text`. | `schemas/qa.py` | R | WEAKER |
| B-88 | Monitoring designed, deferred to a later phase | Later built (Phase 5) | B-20 | R | MATCHES |
| B-89 | No new agent; discovery stays one agent | Holds | repo-wide | R | MATCHES |
| B-90 | Matching a guardrailed agent; hard constraints rule-locked; count 5 | Lock holds; evidence check shallow; exclusions ignored; one LLM call | A-05a to A-05e | R | WEAKER |

#### §36-37 Capability scope labels and the future submission architecture

"Label says" is the Blueprint's label. The mark asks: does the code agree with that label today?

| ID | Capability | Label says | State in code | Proof | Method | Mark |
|---|---|---|---|---|---|---|
| B-100 | Auth + user profile | MVP | Built | `api/auth.py`, `api/profile.py` | R | MATCHES |
| B-101 | Constraint-typed profile (hard / soft / exclusion / missing) | MVP | Built | `services/profile.py:45-109` | R | MATCHES |
| B-102 | Registry + "initial high-quality source set" | MVP | Registry built; 3 of 5 seeds not monitored; seeds have `robots_checked: false` | `seed_sources.yaml` | R | WEAKER |
| B-103 | MCP/API integrations (justified) | MVP | No MCP; one placeholder API connector | `api_connector.py:58` | R | MISSING |
| B-104 | Multi-source discovery + normalize + dedup | MVP | Built, weak | J-05 to J-11 | R | WEAKER |
| B-105 | Official-source verification + provenance | MVP | Label only | V-01 | R | NAME ONLY |
| B-106 | Scholarship DB + Qdrant retrieval | MVP | DB yes; Qdrant only for URL-added pages | R-06 | R | WEAKER |
| B-107 | Profile matching (hard / soft / exclusion) | MVP | Exclusions ignored; 4 hard dimensions | A-05e, S-10 | R | WEAKER |
| B-108 | Scholarship URL matching | MVP | Built | J-20 to J-28 | R | WEAKER |
| B-109 | Q&A + structured responses | MVP | Q&A built; one schema | B-87 | R | WEAKER |
| B-110 | Basic planning + document requirement tracking | MVP | Built | W-05 | R | MATCHES |
| B-111 | Basic CV/SOP assistance | MVP | Built; `.txt`; two document kinds | W-03, W-04 | R | WEAKER |
| B-112 | Coverage summary (basic counts + gaps) | MVP | Built | `services/coverage.py` | R | MATCHES |
| B-113 | Application/Submission agent orchestration | Post-MVP (Phase 4) | Rule table, not an agent | A-04 | R | NAME ONLY |
| B-114 | Full document intelligence (inconsistency detection at depth) | Post-MVP | A GPA inconsistency is flagged; PDF only, no OCR | W-02 | R | WEAKER |
| B-115 | Scheduled monitoring + notifications | Production (Phase 5) | Built; email works only if `SMTP_HOST` is set (unchecked on the VM) | `services/alerts.py:116-143` | R, U | MATCHES |
| B-116 | Coverage analytics dashboards + source health | Production (Phase 5) | Health and coverage are JSON endpoints; no dashboard | `api/health.py`, `api/coverage.py` | R | WEAKER |
| B-117 | Semi-automated candidate validation | Production (Phase 5) | Built, advisory only | `services/candidate_validation.py` | R | MATCHES |
| B-118 | Large-scale expansion | Future → pulled to 006 | Not built; consistent with the roadmap | B-26 | R | MATCHES |
| B-119 | LLM quota governance | Production (003) | Not built; consistent with the roadmap | B-25 | R | MATCHES |
| B-120 | Official-page verification + `listed` status | Production (004) | Not built; consistent | B-56 | R | MATCHES |
| B-121 | Live approval-gated portal submission | Future (Phase 6) | Absent, guarded by a static test | `tests/api/test_no_live_submission.py` | R | MATCHES |
| B-122 | SaaS tenancy, billing, quotas | Future (Phase 6) | Absent | repo-wide | R | MATCHES |
| B-123 | §37 "the system never autonomously performs the final submission" | No send code exists; approvals are append-only, scope-bound and content-fingerprinted | `services/submission_approval.py`; `models/application.py` | R | MATCHES |

#### §38 v1.2 revision notes and consistency audit

| ID | Claim | What the code shows | Proof | Method | Mark |
|---|---|---|---|---|---|
| B-130 | Change 1: per-capability I/O contracts for every button (§5.1) | Contracts exist as OpenAPI; many Output/Next-action items differ (section 7); no buttons exist | `specs/*/contracts/openapi.yaml` | R | WEAKER |
| B-131 | Change 2: comprehensive scholarship schema with per-field value-status | Value-status only on `requirements` rows; discovered rows have almost no fields | `models/requirement.py`; `models/scholarship.py` | R | WEAKER |
| B-132 | Change 3: constraint-typed living profile | Built | `services/profile.py` | R | MATCHES |
| B-133 | Change 4: no-guess value-status discipline | The LLM self-labels `known` and `verified` (T-07) | `tools/extract_requirements.py:88-94` | R | WEAKER |
| B-134 | Change 5: document intelligence strengthened | PDF only; four declared types satisfy | W-02 | R | WEAKER |
| B-135 | Change 6: CV/SOP requirement-awareness (Europass, specified questions) | Specified questions yes; Europass is a heading word | `cv_gen/graph.py:321-329` | R | NAME ONLY |
| B-136 | Change 8: matching uses hard / soft / exclusion differently; Strong / Possible / Not | Exclusions do not change the result | BR-08 | M | WEAKER |
| B-137 | Change 9: coverage metrics for **countries / universities / providers** | Countries only (S-07) | `schemas/discovery.py:29-48` | R | WEAKER |
| B-138 | Change 10: lifecycle / freshness first-class | Nine states, deterministic, tested | `services/lifecycle.py:50-129` | R | MATCHES |
| B-139 | Change 11: response schemas expanded (eligibility / funding / matching) | Only matching has its own | B-87 | R | WEAKER |
| B-140 | Audit: "registry governs connectors; **MCP is registry-bound**" | No MCP | M-01 | R | MISSING |
| B-141 | Audit: "fresh facts verified against the official source; freshness first-class" | Freshness yes (lifecycle); verification against the official page no | V-01 | R | WEAKER |
| B-142 | Audit: "§5.1 contracts map 1:1 to agents/workflows/services; OpenAPI generated from them" | OpenAPI is generated from the code, not from the contracts; drift tests keep them in step | `tests/api/test_openapi_contract_002.py` | R | WEAKER |
| B-143 | Audit: application workflow and submission safety consistent | Yes | B-123 | R | MATCHES |

#### §39-45 Execution model, handoff artifacts, revision notes v1.4-v1.6

| ID | Claim | What the repo shows | Proof | Method | Mark |
|---|---|---|---|---|---|
| B-150 | §39-40 Manager/Worker and Workstreams | A process, not code. Work is organised as spec-kit features (`001`, `002`, `003`), not `WS0.1`… workstreams. | `specs/` | R | WEAKER |
| B-151 | §41 Charter is tier T0 at `specs/charter.md` | Exists | `specs/charter.md` | R | MATCHES |
| B-152 | §41 layout `specs/phase-N/workstreams/WSN.x.md` | Absent | `ls specs` | R | MISSING |
| B-153 | §41-42 `specs/handoffs/WSN.x.summary.md` and `specs/handoffs/manager-state.md` | Absent. Continuity lives in `history/prompts/` (PHRs) and `docs/` instead. | `ls specs`, `ls history/prompts` | R | MISSING |
| B-154 | §44 (v1.5) "the continuity model is now complete across all three axes" | Defined in text; none of the three artifacts exists in the repo | B-152, B-153 | R | MISSING |
| B-155 | §45 #3: Harness needs an isolated **sandbox** for tool/browser execution | No sandbox exists (and §45 lists it as undecided) | §45 NOT YET DEFINED #1 | R | MISSING |
| B-156 | §45 #4-5: evals with a scored CI gate, established early | Gate exists from Phase 1; thresholds are fixed numbers, not regression checks (E-07) | `evals/runner.py:26-28`; `ci.yml:75-77` | R | WEAKER |
| B-157 | §45 #6: first real evals = matching, Q&A, **checker-accuracy** | Checker-accuracy absent | E-06 | R | WEAKER |
| B-158 | §45 #7: minimal cloud runtime at end of Phase 2 | Built (B-14) | B-14 | R | MATCHES |
| B-159 | §45 NOT YET DEFINED #1-4 (sandbox, eval thresholds, cloud provider, FDE README) | Correctly listed as open; the provider is in fact chosen (Oracle VM) and the thresholds are in code | `runner.py:26-28` | R | MATCHES |

#### §46 v1.7 revision notes

| ID | Claim | What the code shows | Proof | Method | Mark |
|---|---|---|---|---|---|
| B-170 | #2 `listed` noted beside the enums (§14) | A doc statement; code has no `listed` (a 004 task) | `models/scholarship.py:43-46` | R | MATCHES |
| B-171 | #3 provider, free-tier limit, central budget, embeddings drift flagged | The drift is real (Gemini embeddings); budget unbuilt | `core/llm.py:11-13` | R | MATCHES |
| B-172 | #4 Phase 5 status and the 360-minute stopgap | Repo default is 60; the VM value is unknown | `core/config.py:48` | R, U | UNCHECKED |
| B-173 | #8 operator-only approval recorded | Not in code (004) | `app/models/user.py` | R | MISSING |
| B-174 | #9 country names and aliases in matching | Not in code (005); today the filter is an exact ISO-code match | `data/repositories/source_repo.py:58-59` | R | MISSING |
| B-175 | #10 meaning of `verified` tightened | Code still uses the old meaning (`mark_confirmed_by_monitoring`) | `scholarship_repo.py:91-106` | R | MISSING |
| B-176 | #11 LLM provider failure row (§33): provider outcome kept out of source health | Not built (003); a quota error currently counts as a failed fetch | `workflows/source_monitor/graph.py:386-395` | R | MISSING |
| B-177 | #12 cost-governed monitoring (fingerprint skip, cadence) | Not built (003) | `specs/003-*/tasks.md` | R | MISSING |
| B-178 | Audit: "ADR-0009 superseded in part by ADR-0013" | ADR-0013 says it "amends" ADR-0009, but ADR-0009 itself has no note pointing to it | `adr/0009-*.md`, `adr/0013-*.md:7` | R | WEAKER |
| B-179 | Audit: "ADR-0008's failed-run definition amended by ADR-0014" | ADR-0014 states it; ADR-0008 carries no pointer | `adr/0014-*.md:59` | R | WEAKER |
| B-180 | Open item #2: embeddings provider undecided | Accurate: code uses Gemini | `core/llm.py:13` | R | MATCHES |
| B-181 | Open item #3: DAAD, SH, KAUST seeded but `discovery_role=false` | Accurate. But 002's tasks T162 and T168 say they were flipped to `true`; the code and its own test say `false` (P2.3). | `seed_sources.yaml:20,78,136`; `tests/services/test_source_registry_seed_002.py:38-43` | R | MATCHES |
| B-182 | Open item #4: EACEA "136 of 136 recent attempts yielded 0 grounded candidates" | Needs the database | command VR-12 | U | UNCHECKED |
| B-183 | Header: gap analysis "77 requirements, 35 Built, 26 Partial, 5 Missing, 11 Deferred" | Part 1 re-judged the same ground with stricter marks and finds more gaps (section 15) | `docs/blueprint-gap-analysis.md` | R | WEAKER |

---

### P2.3 Task lists in `specs/001`, `specs/002`, `specs/003`

How each task was checked: the file the task names exists; the identifiers the task text names are in those files (a script, `S`); and for every task whose claim is about behaviour I read the code (`R`). I did **not** run these tasks' tests against a database, so "Done" means "written and, where I looked, behaving as described", not "its test passes right now" (that is P2.6 and P2.7).

Totals: **001** has 140 tasks (69 ticked, 71 unticked). **002** has 110, all ticked. **003** has 76, none ticked. Ranges below cover every ID. Two ID oddities: T135 sits in Phase 2 but is numbered after T134, and T249/T250 are appended to 002's phases.

#### 001, MVP core platform

| Task | Claimed | Real | Proof | Method |
|---|---|---|---|---|
| T001-T011 (foundation: uv project, FastAPI + `/health`, package skeleton, Docker, CI, eval skeleton, charter, DB + Alembic, users migration, JWT auth, tests) | ticked | Done | `backend/pyproject.toml`; `app/main.py`; `Dockerfile`, `docker-compose.yml`; `.github/workflows/ci.yml`; `evals/runner.py`; `specs/charter.md`; `migrations/versions/fed0167c65c9_*`; `api/auth.py`; `tests/test_auth.py`, `test_health.py`, `test_db_connection.py` | S, R |
| T012 shared `ValueStatus` / `Confidence` / `FieldValue` | **not ticked** | **Done** | `app/schemas/common.py` (all three names present) | S |
| T013-T019 Qdrant wrapper, loaders, chunking, embed tool, embed pipeline, retrieval, `ground_check` | not ticked | **Done** | `data/vectors/qdrant_client.py`; `rag/loaders.py`, `chunk.py`, `embed.py`, `retrieve.py`; `tools/embed.py`, `tools/ground_check.py:32` | S, R |
| T020 RAG grounding-check node | not ticked | **Done but unused** | `rag/grounding.py:20` `check_grounding` is defined and has no caller; Q&A imports `retrieve` and `ground_check` directly (`agents/research_qa/agent.py:8,11`). T058 says it uses T020; it does not. | R |
| T021-T023 profile tests (API, flow, service) | not ticked | **Done** | `tests/api/test_profile_api.py`, `tests/integration/test_profile_flow.py`, `tests/services/test_profile_service.py` | S |
| T024-T031 profile models, migration, repo, service, schemas, `extract_profile`, API, wiring | not ticked | **Done** | `models/profile.py`; `migrations/versions/6e1035fe0cc6_create_profile_tables.py` (T025's `{rev}` placeholder is this file); `profile_repo.py`; `services/profile.py`; `api/profile.py`; routes `GET,PUT /profile`, `POST /profile/criteria`, `POST /profile/import-cv` | S, R |
| T032-T036 US2 tests (contract, flow, verdict matrix, guardrails, ranking) | not ticked | **Done** | `tests/api/test_matching_api.py`; `tests/integration/test_url_match_flow.py`; `tests/services/test_hard_constraints_matrix.py`; `tests/agents/test_matching_guardrails.py`; `tests/services/test_ranking.py` | S |
| T037-T048 scholarship / requirement / funding / match models, migration, repos, `web_fetch`, `extract_requirements`, `hard_constraints`, `ranking`, schemas | not ticked | **Done**, with the weaknesses in Part 1 (T-03, T-07, S-10) | `models/scholarship.py`; `migrations/versions/603848a52d5b_*` (T041's placeholder); `tools/web_fetch.py`; `services/hard_constraints.py:179-199` | S, R |
| T049 `url_match` workflow "fetch → extract → **verify** → hard_constraints → matching → persist" | not ticked | **Partly** | The graph has no verify node: `fetch → extract → persist_scholarship → ingest_rag → hard_constraints → matching → persist_match` | R |
| T050 Matching agent (schema-locked, evidence-required, no-guess, no web tools) | not ticked | **Done**; "evidence-required" only checks the string is non-empty (A-05c) | `agents/matching/agent.py:105-107` | R |
| T051-T053 URL-match and matching endpoints, wiring | not ticked | **Done** | routes `POST /scholarships/match-url`, `POST /scholarships/{id}/match`, `GET /matches` (live `app.openapi()` dump) | R |
| T054-T055 Q&A tests | not ticked | **Done** | `tests/api/test_qa_api.py`, `tests/integration/test_qa_flow.py` | S |
| T056 Q&A groundedness eval case | ticked | Done | `evals/cases/qa_groundedness.py` | S |
| T057, T059, T060 `QaAnswer`, Q&A endpoint, wiring | not ticked | **Done** | `schemas/qa.py`; `POST /scholarships/{id}/qa` | R |
| T058 Q&A agent "grounded via retrieve+grounding (T018/T020), cites `source_url` / `last_verified_at`" | not ticked | **Partly** | Retrieval and lexical `ground_check` are used; T020 is not; there is no escalation to a live fetch | R |
| T061 Main orchestrator (7 intents) | ticked | **Done, NOT REACHABLE** | all 7 intents are implemented (`orchestration/main_agent.py:38-45,252-286`); no route or other code calls `handle_message` | R |
| T062 routing integration test | ticked | Done | `tests/integration/test_main_agent_routing.py` (20 tests, 4 need the DB) | S |
| T063 eval gate in CI | ticked | Done | `evals/runner.py`; `.github/workflows/ci.yml:75-77` | R |
| T064 discovery contract test | not ticked | **Done** | `tests/api/test_discovery_api.py` | S |
| T065 US4 acceptance-scenario integration test | not ticked | **Not done** | the named file `tests/integration/test_discovery_flow.py` does not exist. Pieces are covered elsewhere (`tests/api/test_discovery_api.py`, `tests/agents/test_discovery_agent.py`, `tests/sources/test_candidate_source_discovery.py`) but no test walks the five scenarios. | S |
| T066 registry-compliance tests | ticked | Done | `tests/sources/test_source_registry_compliance.py` | S |
| T067-T068 conflict-resolution and coverage unit tests | not ticked | **Done** | `tests/services/test_conflict_resolution.py`, `test_coverage.py` | S |
| T069-T072 source models, migration, repo, retry policy | ticked | Done | `models/source.py`; `migrations/versions/d501fadbb3f1_*`; `sources/retry_policy.py:14-16` | S, R |
| T073 connector wrappers reject non-active sources | ticked | Done; `api_connector` transport is a placeholder | `sources/connectors/official_fetch.py:139-150`; `api_connector.py:58` | R |
| T074 `official_fetch` tool | ticked | Done | `tools/official_fetch.py` | S |
| T075 `search` tool "scoped to governed sources" | ticked | **Partly**: the domain filter is real, the backend raises `NotImplementedError`, nothing calls it | `tools/search.py:40` | R |
| T076 `api_connector` tool | ticked | **Partly**: wrapper exists; no API provider is onboarded | `tools/api_connector.py` | R |
| T077-T083 `classify` tool and the six services | not ticked | **Done** (behaviour: T-05, S-02, S-04, S-08, S-09) | `tools/classify.py`; `services/{source_registry,normalize,classify,dedup,verification,conflict_resolution}.py` | S, R |
| T084 coverage service | ticked | Done | `services/coverage.py` | S |
| T085 `rerank` tool | ticked | **Done, NOT REACHABLE** | `tools/rerank.py:40`; no caller | R |
| T086 `source_validate` workflow | ticked | **Done, NOT REACHABLE** | `workflows/source_validate/graph.py:56-96`; callers are tests only | R |
| T087 `ingestion` workflow | ticked | Done, weaker than the Blueprint (W-07) | `workflows/ingestion/graph.py:641-673` | R |
| T088-T092 discovery schemas, agent, endpoint, orchestrator route, wiring | ticked | Done (orchestrator route unreachable) | `schemas/discovery.py`; `agents/discovery/agent.py`; `POST /discovery` | R |
| T135 listing-page extraction; "Resolves US4 Scenario 1 (**ranked** results across ≥2 source types)" | ticked | **Partly**: extraction is real and grounded, but `POST /discovery` does not rank (J-10) | `tools/extract_listing.py:112,141`; `api/discovery.py:37-60` | R |
| T093-T097 US5 tests and evals | ticked | Done | `tests/api/test_documents_api.py`, `test_generation_api.py`; `tests/integration/test_document_generation_flow.py`; `evals/cases/generation_groundedness.py`; `tests/tools/test_ground_check.py` | S |
| T098-T108 applications and document models, migration, repos, storage, `pdf_parse`, `doc_pipeline`, schemas, endpoints | ticked | Done; `422` on a bad PDF is real | `models/application.py`, `models/document.py`; `migrations/versions/b53c6b5689ba_*`; `data/files/storage.py`; `api/documents.py:87` | S, R |
| T109 `cv_gen` "requirement-aware, **format-specific (Europass)**" | ticked | **Partly**: grounded and gated, but Europass changes one word in the heading and the file is `.txt` | `workflows/cv_gen/graph.py:321-329` | R |
| T110-T112 `sop_gen`, generation endpoints (`409` on a gap), wiring | ticked | Done | `workflows/sop_gen/graph.py:74,338-354`; `api/generation.py:67,91` | R |
| T113 tracker + plan contract test | ticked | Done | `tests/api/test_application_api.py` | S |
| T114-T116 assistant, flow, approval-scope tests | not ticked | **Done** | `tests/api/test_application_assistant_api.py`; `tests/integration/test_application_planning_flow.py`; `tests/services/test_submission_approval_scope.py` | S |
| T117 no-live-send static test | ticked | Done | `tests/api/test_no_live_submission.py` | S |
| T118-T120 tasks and approvals models, migration, repo | ticked | Done | `models/application.py`; `migrations/versions/ad4826bd57e6_*` | S |
| T121 `readiness` service ("never hand-edited") | not ticked | **Done** (branches in P2.4) | `services/readiness.py:157-281` | R |
| T122-T123 `app_plan`, `submit_prep` | not ticked | **Done**; the "approval gates" are database lookups, not pauses (W-06) | `workflows/app_plan/graph.py:112-123`; `workflows/submit_prep/graph.py:160-170,201-214` | R |
| T124 Application agent "drafts / assembles / stages materials" | not ticked | **Partly**: it only reports the next step; it drafts nothing | `agents/application/agent.py:1-5,63-108` | R |
| T125 plan schemas | ticked | Done | `schemas/plan.py` | S |
| T126-T129 tracker, plan, assistant, approval endpoints; orchestrator route; wiring | not ticked | **Done** (orchestrator route unreachable) | routes `GET,POST /applications`, `POST /applications/{id}/plan`, `…/assistant/next-step`, `…/submission-approvals` | R |
| T130 quickstart validation, "verified live end to end" | ticked | **Partly**: the log exists; the task text itself lists three scenarios still not demonstrated live | `docs/t130-verification-log.md`; `specs/001-*/tasks.md:263` | R |
| T131 code-review pass: no LLM path reaches `hard_constraints` / `ranking` | ticked | **Done**: neither module imports any LLM, agent or tool code; `hard_constraints` is imported only by `services/matching.py` and `url_match` | `services/hard_constraints.py`, `services/ranking.py` imports; `grep` of importers | R |
| T132 every repository query is `user_id`-scoped | ticked | **Done** for what I opened: the six profile mutators check ownership | `data/repositories/profile_repo.py:39-130` | R |
| T133, T136 OpenAPI drift fixes | ticked | Done; Part 1 section 7 re-diffed the live routes against both contract files | `docs/full-project-audit.md` §7 | R |
| T134 full eval run and gate | ticked | Done: the runner has three cases and a CI step; the quoted scores were not re-run | `evals/runner.py:70-77`; `ci.yml:75-77` | R |
| T137 source-registry-compliance eval case | ticked | **Not built (decided against)**; the text says "INVESTIGATED, REJECTED". The tick records the decision, not a build. | no such file in `evals/cases/` | S |
| T138 live Qdrant + Gemini smoke tests and `deep-ci.yml` | ticked | **Partly**: smoke tests and workflow exist; the task text says the repo secrets were never added, so the workflow has never run on GitHub | `tests/smoke/*`; `.github/workflows/deep-ci.yml` | R, U |
| T139 replace `HTTP_422_UNPROCESSABLE_ENTITY` | ticked | Done | no `UNPROCESSABLE_ENTITY` left in `app/` or `tests/` | R |
| T140 stop fetching the homepage when a listing URL is set | **not ticked** | **Done** (by 002 T164-T166) | `sources/connectors/official_fetch.py:93-109` (listing column → legacy key → homepage); `agents/discovery/agent.py:215` passes `None` so the connector chooses | R |

#### 002, source monitoring and hardening

| Task | Claimed | Real | Proof | Method |
|---|---|---|---|---|
| T141-T144 dependencies and settings | ticked | Done | `backend/pyproject.toml:8,18,35`; `app/core/config.py:38-56`; `.env.example:13-28`; `tests/core/test_config_002.py` | S, R |
| T145-T153 tests for models / migrations / repo / lifecycle, new models, registration | ticked | Done | `models/monitoring.py`, `alert.py`, `candidate_validation.py`; `models/__init__.py:6-13`; `tests/data/test_002_models.py`, `test_002_migrations.py`, `tests/services/test_lifecycle.py` | S |
| T154-T155 two hand-written migrations | ticked | Done: chain is linear, one head | `c3a9f1d27b64` (down `ad4826bd57e6`) → `e81b5c40d9a2` → `f2b8d4a6c1e3` (head) | R |
| T156-T158 schemas, `log_fetch`, lifecycle service | ticked | Done | `schemas/source.py`; `source_repo.py`; `services/lifecycle.py:50-129` | S, R |
| T249 record `read_at` in the 002 spec docs | ticked | Done | `tests/api/test_openapi_contract_002.py` exists as the drift check | S |
| T159-T163 US6 tests | ticked | Done | `tests/sources/test_listing_url_resolution.py`; `tests/integration/test_listing_page_fetch_flow.py`; `tests/agents/test_discovery_agent.py` | S |
| **T162, T168** "flip `discovery_role` to **true** for DAAD, Stipendium Hungaricum and KAUST" | ticked | **Not done, and later reversed on purpose**: all three are `false`; the test and the seed comment say they stay `false` until a live check and a robots review | `seed_sources.yaml:20,78,136`; `tests/services/test_source_registry_seed_002.py:38-43`; Blueprint §46 open item #3 | R |
| T164-T167, T169 URL resolution, tool wrapper, T140 fix, seed loader | ticked | Done | `official_fetch.py:93-109`; `tools/listing_fetch.py`; `services/source_registry_seed.py:28` | R |
| T170-T173 monitor tests, AST guardrails, scheduler/lock tests, API tests | ticked | Done (T172/T173 files are named `test_scheduler.py` + `test_locks.py`, `test_monitoring_api.py`, as the task's own tick-sync note says) | `tests/workflows/test_source_monitor.py` (57 tests), `test_source_monitor_guardrails.py`; `tests/scheduling/`; `tests/api/test_monitoring_api.py` | S |
| T174-T177 schemas, monitoring repo, lock helper, `bypass_cache` | ticked | Done (the repo function is `get_fetch_logs_for_run`, not `get_run_outcomes`; the API uses an atomic `create_run_if_none_running` instead of `has_running_run`) | `data/repositories/monitoring_repo.py:18-103`; `scheduling/locks.py` | R |
| T178 workflow skeleton "no LLM, no agent imports" | ticked | **Done, but the claim is only true of the module**: the workflow imports no LLM, yet every check calls `fetch_and_extract_listing`, which calls the LLM | `workflows/source_monitor/graph.py:447` (`bypass_cache=True`); `official_fetch.py` | R |
| T179-T180 change detection, stale sweep | ticked | Done | `source_monitor/graph.py:237,361-376` | R |
| T181-T183 monitoring router, scheduler, wiring | ticked | Done: `202` + `409`, interval job `max_instances=1`, lifespan start/stop | `api/monitoring.py:67-80`; `scheduling/scheduler.py:53-77`; `app/main.py:35-62` | R |
| T184-T196, T250 alerts: tests, schemas, repo, channels, owner selection, router, `unread_only`, mark-read | ticked | Done (branches in P2.4) | `services/alerts.py`; `api/alerts.py:20-24`; routes `GET /alerts`, `GET,PUT /alerts/preferences`, `POST /alerts/{id}/read` | R |
| T197-T204 health: tests, schemas, helpers, router, failure rule, recovery | ticked | Done: failing only through the consecutive-run rule; recovery via `reactivate_source` | `api/health.py`; `source_monitor/graph.py:388-394` | R |
| T205-T211 coverage | ticked | Done, as specified (countries only; that was the 002 scope) | `services/coverage.py`; `api/coverage.py` | R |
| T212-T215 lifecycle wiring and walk test | ticked | Done | `tests/integration/test_lifecycle_walk.py`; `source_monitor/graph.py:237,361-376` | S, R |
| T216-T224 US8 tests (rate limit, logs, metrics, retention, deploy config, sanitization, secrets, retry, upload) | ticked | Done | `tests/core/test_rate_limit.py`, `test_logging.py`, `test_metrics.py`; `tests/services/test_retention.py`; `tests/infra/test_deploy_config.py`; `tests/sources/test_monitor_sanitization.py`; `tests/test_no_secrets_in_repo.py` | S |
| T225-T231 rate limiter, JSON logs, metrics (bearer-protected), retention, run logging, daily job, wiring | ticked | Done | `core/rate_limit.py:97-110`; `core/metrics.py:7,19`; `services/retention.py`; `scheduler.py:67-76`; `main.py:57-62` | R |
| T232-T233 Caddy and the persistent volume | ticked | Done: host networking, backend on `127.0.0.1`, `/metrics` answered 404 by Caddy, storage volume mounted | `Caddyfile:17-24`; `docker-compose.podman.yml:21-60` | R |
| T234 `pip-audit` in CI | ticked | Done | `.github/workflows/ci.yml` (the audit step and its two documented ignores) | R |
| T235-T237 upload failure surfaced, sanitisation at ingest, ops doc | ticked | Done | `api/documents.py`; `source_monitor/graph.py:134-150`; `docs/operations-hardening.md` | R |
| T238-T245 candidate validation (tests, schema, repo, service, workflow step, route) | ticked | Done | `services/candidate_validation.py`; `source_monitor/graph.py:497-521`; route `GET /sources/candidates/{id}/validations` | R |
| T246-T248 drift test, full run, quickstart log | ticked | Done: files exist. The "full backend suite 1015 passed" is the log's number, not re-run here | `tests/api/test_openapi_contract_002.py`; `docs/t-002-verification-log.md:24` | S |

#### 003, quota governance (T251-T326)

Everything below is **Not done**. Proof for every row: the named new file does not exist, and none of the feature's names appear anywhere under `backend/app/` (`llm_gate`, `llm_budget`, `check_state`, `content_fingerprint`, `cadence`, `source_rules`, `rule_extract`, `LlmBudgetDeferred`). No new setting is in `core/config.py`, `SourceFetchLog.outcome` and `MonitoringRun.sources_skipped` do not exist, and `core/llm.py` has no gate.

| Task | Claimed | Real | Note | Method |
|---|---|---|---|---|
| T251-T252 settings tests and settings | not ticked | Not done | none of the 003 settings in `core/config.py` | R |
| T253-T263 schema, migration, gate interface, repos, `outcome` field | not ticked | Not done | no `core/llm_gate.py`, `models/llm_budget.py`, `models/check_state.py`, no 003 revision after head `f2b8d4a6c1e3` | R |
| T264-T276 central budget gate, provider wrapper, entry-point scopes, doc pipeline, live-drift contract | not ticked | Not done | `core/llm.py:33,48` call the provider with no gate. **T275** has a precursor: the doc pipeline already catches `genai_errors.APIError` and returns `extraction_unavailable` (`workflows/doc_pipeline/graph.py:143-153`), but `LlmError` does not exist | R |
| T277-T286 fingerprint skip and conditional fetch | not ticked | Not done | no `services/content_fingerprint.py`; `tools/web_fetch.py` sends no validators | R |
| T287-T293 provider failures kept out of health | not ticked | Not done | a quota error still counts as a failed check (B-176) | R |
| T294-T301 cadence by update frequency | not ticked | Not done | no `services/cadence.py`; the scheduler is one global interval | R |
| T302-T309 budget and cadence visibility; retention of the ledger | not ticked | Not done | `core/metrics.py` and `services/retention.py` have no ledger code | R |
| T310-T321 deterministic extraction rules (two OWNER steps) | not ticked | Not done | no `tools/rule_extract.py`, `services/source_rules.py`, HEC fixture or parity report | R |
| T322-T326 runbook, gap-analysis update, full run, rollout notes, interval decision (OWNER) | not ticked | Not done | | R |

#### What the task lists say about progress, in one place

| List | Boxes | Done as claimed | Partly | Decided against (not built) | Not done | Notes |
|---|---|---|---|---|---|---|
| 001 ticked | 69 | 62 (3 of them, T061, T085, T086, are not reachable from any route) | 6 (T075, T076, T109, T130, T135, T138) | 1 (T137: rejected, no eval built) | 0 | |
| 001 unticked | 71 | 67 (incl. T020 which nothing uses, and T140) | 3 (T049, T058, T124) | 0 | 1 (T065) | the unticked boxes are a stale record, not missing work |
| 002 ticked | 110 | 108 | 0 | 0 | 2 (T162, T168: reversed by a later decision) | T178's "no LLM" is true of the module only |
| 003 unticked | 76 | 0 | 0 | 0 | 76 | matches the Blueprint ("not implemented") |
| **All** | **326** | **237** | **9** | **1** | **79** | |


---

### P2.4 `services/readiness.py`, `services/alerts.py`, `services/retention.py`: every branch

These three files were read line by line. The scheduler that runs retention was read too (`scheduling/scheduler.py`). Nothing here was run against a database (method R), except where P2.5 says otherwise.

#### `services/readiness.py` (281 lines)

| ID | Branch | What it does | Proof | Mark |
|---|---|---|---|---|
| RD-01 | `value_status` is `not_applicable` or `conditional` | `NEEDS_HUMAN_REVIEW` | `readiness.py:161-162` | MATCHES |
| RD-02 | `value_status` is `unknown` / `conflicting`, or `confidence != "verified"` | `NEEDS_OFFICIAL_VERIFICATION`. For a requirement from `POST /scholarships/match-url` this gate is passed whenever the **LLM labelled itself** `confidence="verified"` (T-07), so the check protects only against values the model itself marked unsure. | `readiness.py:164-165`; `workflows/url_match/graph.py:99-107` | WEAKER |
| RD-03 | a document already `satisfies_requirement_id` this requirement | `COMPLETE`. Depends on `doc_pipeline` satisfaction, which handles only four declared types (W-02). | `readiness.py:167-168,244` | MATCHES |
| RD-04 | a document of the right type exists but is not confirmed to satisfy | `NEEDS_HUMAN_REVIEW`; never an invented pass or fail | `readiness.py:170-171,245` | MATCHES |
| RD-05 | material is system-generatable: a draft exists / does not | `NEEDS_HUMAN_REVIEW` / `AI_CAN_GENERATE`. The set includes personal statement, study plan, research proposal, motivation letter and Europass CV, but no code generates the last four (`GeneratedDocumentType.MOTIVATION` is never written), so the label promises an action the system cannot take. | `readiness.py:77-85,112-117,173-176`; `api/generation.py` has two routes | WEAKER |
| RD-06 | material is user-held (portfolio, publication, financial) | `MISSING` | `readiness.py:178-179` | MATCHES |
| RD-07 | material is third-party-issued: profile test status `have` / anything else | `MISSING` / `USER_MUST_OBTAIN`. The `have` test status is read only for language and test categories. | `readiness.py:181-184,252-254,203-218` | MATCHES |
| RD-08 | catch-all (material unresolved) | `NEEDS_HUMAN_REVIEW`. Every path returns a label; no path returns `None` or raises. | `readiness.py:186` | MATCHES |
| RD-09 | `resolve_material(key, category)` | Matches the requirement key only if it equals a `DocumentType` value after `strip().lower()`. A model-written key such as "statement of purpose" or "recommendation letters" misses and falls to the category default, or to the catch-all. | `readiness.py:122-131` | WEAKER |
| RD-10 | `compute_checklist_for_application`: due dates | `ChecklistItemDraft.due_date` exists but is never given a value, so every task's `due_date` is null. | `readiness.py:200,272-280`; `app_plan/graph.py:90` | WEAKER |
| RD-11 | `compute_checklist_for_application`: scholarship missing | Silently produces an empty checklist instead of an error. Unlikely in practice (foreign key), but not guarded. | `readiness.py:230-231` | WEAKER |

#### `services/alerts.py` (339 lines)

| ID | Branch | What it does | Proof | Mark |
|---|---|---|---|---|
| AL-01 | `InAppChannel.send` | Sets `delivered_in_app_at`; always succeeds | `alerts.py:86-89` | MATCHES |
| AL-02 | `EmailChannel.send`: no `SMTP_HOST` / no recipient / no `ALERTS_FROM_EMAIL` | Records an explicit `email_error`, returns False, never raises | `alerts.py:117-125` | MATCHES |
| AL-03 | `EmailChannel.send`: send path | Always `STARTTLS`; a server on implicit TLS (port 465) or without STARTTLS cannot be used. Every exception is captured to `email_error` (truncated to 500 characters). | `alerts.py:132-143` | WEAKER |
| AL-04 | `_email_body` | Contains the scholarship name and id only. There is no link, because there is no frontend. | `alerts.py:64-70,92-106` | WEAKER |
| AL-05 | `decide_alert`, prior match exists, hard-constraint results unchanged | No alert (soft drift never alerts) | `alerts.py:198-201` | MATCHES |
| AL-06 | `decide_alert`, prior match exists, results changed | `eligibility_changed` alert. The comparison is of the new run against the last stored match, so a **profile edit** made between the two matches also changes the set and is reported as if the scholarship had changed. | `alerts.py:199-209` | WEAKER |
| AL-07 | `decide_alert`, no prior match | Alerts only on `eligible` / `likely` / `possibly`. A hard-constraint list that contains `deadline = unknown` yields `unknown_requires_verification`, which is not on the list, so **a scholarship with no deadline never produces a "new match" alert** (BR-06). Listing-only records have no deadline. | `alerts.py:49,211-213`; `hard_constraints.py:37-38`; BR-06 | WEAKER |
| AL-08 | `dispatch_alert` | Unknown channel is logged and skipped; a channel that raises is logged; the row is saved either way. Also the retry path (updates the same row). | `alerts.py:219-233` | MATCHES |
| AL-09 | `_owners_for` | New scholarship: every user with a profile. Changed scholarship: users with a prior match or an application. | `alerts.py:239-242`; `alert_repo.py:96-105` | MATCHES |
| AL-10 | `_process_owner`: all channels disabled | Re-match already ran; only delivery is suppressed; returns False | `alerts.py:271-274` | MATCHES |
| AL-11 | `_process_owner`: every call re-matches | `match_scholarship` always **inserts a `matches` row**, even when no alert follows, and may make an LLM call when the profile has soft criteria. So each owner × event costs a database row and possibly one Gemini request. | `alerts.py:265`; `services/matching.py:35-58` | WEAKER |
| AL-12 | `process_changes`: duplicate check | Skips a (user, scholarship, run) that already has an alert. The docstring itself says it is not safe across concurrent runs. | `alerts.py:316-321` | MATCHES |
| AL-13 | `process_changes`: budget exhausted | Logs once and **returns**, ending the whole run's alerting. The remaining owners and events are never retried, because the change has already been stored and will not be detected again. With the default cap of 500 pairs, a large user base silently loses alerts. | `alerts.py:322-330`; `core/config.py:44` | WEAKER |
| AL-14 | `process_changes`: one owner fails | Logged, session rolled back, counted in `errors`, loop continues | `alerts.py:332-338` | MATCHES |
| AL-15 | `process_changes`: event whose scholarship id is missing | Skipped silently | `alerts.py:312-314` | MATCHES |

#### `services/retention.py` (166 lines) and its scheduler job

| ID | Branch | What it does | Proof | Mark |
|---|---|---|---|---|
| RT-01 | Argument checks | `retention_days`, `window_n`, `batch_size` below 1 raise `ValueError` | `retention.py:97-98` | MATCHES |
| RT-02 | Empty `source_ids` | Returns 0; never means "all sources" | `retention.py:99-101` | MATCHES |
| RT-03 | Floor (a) | Keeps every row of each source's last `HEALTH_WINDOW_N` monitoring **runs** (so a run's 2-3 rows survive together) | `retention.py:53-64` | MATCHES |
| RT-04 | Floor (b) | Keeps each source's single latest row of any kind | `retention.py:65-69` | MATCHES |
| RT-05 | Floor (c) | Keeps each source's latest OK row that has `items_found` (the extraction baseline) | `retention.py:70-75` | MATCHES |
| RT-06 | Protected-id snapshot | Computed once, reused across batches; a stale snapshot can only keep too many rows | `retention.py:43-48` | MATCHES |
| RT-07 | Prunable predicate | Older than the cutoff **and** `id != ALL(protected)`. With an empty protected list `<> ALL('{}')` is true for every row, so correctness relies on floor (b) always protecting at least one row per source that has rows. Not executed against PostgreSQL here. | `retention.py:106-113` | MATCHES |
| RT-08 | `dry_run` | Counts what would be deleted, deletes nothing | `retention.py:114-115` | MATCHES |
| RT-09 | Delete loop | One batch per transaction; stops when a batch deletes fewer rows than `batch_size`. A failure midway leaves earlier batches committed (intended). | `retention.py:117-130` | MATCHES |
| RT-10 | Scope | Touches only `source_fetch_log`. It does **not** prune `monitoring_runs`, `alerts`, `matches`, or `qdrant` chunks, and rows with `NULL monitoring_run_id` are prunable by age. | `retention.py:1-31` | MATCHES |
| RT-11 | Scheduler coupling | The daily 03:00 UTC job exists only if `MONITORING_INTERVAL_MINUTES > 0`. Turn monitoring off and retention silently stops. (Documented in the module docstring, not in `.env.example`.) | `scheduling/scheduler.py:8-9,53-57,67-76` | WEAKER |
| RT-12 | Scheduled run failure | Caught and logged; retried the next day | `scheduler.py:42-50` | MATCHES |
| RT-13 | CLI | `python -m app.services.retention --dry-run [--source-id …]` prints a count | `retention.py:148-162` | MATCHES |

---

### P2.5 Every Part 1 item marked "(by reading)", now confirmed by a run

Method: `docs/audit-part2-scripts/mock_run.py`. It imports the real application code, replaces `generate_text`, `fetch_url`, the extractor, Qdrant, `embed_documents`, ingestion and the repositories with counting fakes, and makes any socket connection raise. It printed "No network attempt occurred". Output, trimmed to the lines used:

```
T-05: LLM calls per classified candidate
  name only + funding unknown:                  LLM calls=1 (rules resolved degree and field)
  all listing fields filled:                    LLM calls=1 (funding, degree still unresolved)
  country+field+degree+funding, no provider:    LLM calls=1
S-10 / MA-03 / A-05e:
  GPA requirement value '3.0 or equivalent' -> ValueError: could not convert string to float
  no rules + no deadline     -> [deadline: unknown] -> verdict unknown_requires_verification
  no rules + future deadline -> [deadline: pass]    -> verdict likely
  same, exclusions_triggered=['requires GRE'] -> likely / possible | exclusions echoed
W-01b: url_match on an already-stored URL
  call 1: extract(LLM) calls=1, Qdrant ingests=1, new scholarship rows=0
  call 2: extract(LLM) calls=2, Qdrant ingests=2, new scholarship rows=0
R-05: re-ingest of one page: chunks per ingest=2,2; points written=4; distinct ids=4; nothing deleted
V-07: discovery: plan steps=8 for 2 sources (4 distinct listings); result rows=16; distinct ids=4
```

| ID | Part 1 item | Result | Method | Confirmed? |
|---|---|---|---|---|
| BR-01 | W-01b: repeat URL still spends an LLM call and a Qdrant ingest | Confirmed. Two calls with the URL already stored made two extractions and two ingests and created no row. `graph.py` orders `extract` before the lookup inside `persist_scholarship`. | M | Yes |
| BR-02 | T-05 / §15 #14: classify adds about one LLM call per candidate | Confirmed under mocks for three realistic candidates: **1 call each**. Funding never resolves by rules (the joined text holds `fully_funded`, which the pattern cannot match), degree `MS` is not matched, provider type is rarely present. The real rate also depends on whether the call is made at all: a failing LLM call is swallowed (`tools/classify.py:140`). Not measured on real pages. | M | Yes, for these samples |
| BR-03 | S-10: `float()` on a text value crashes | Confirmed: `ValueError`. The route `POST /scholarships/{id}/match` has no `try`, so it surfaces as HTTP 500 (traced, not executed through HTTP). | M, R | Yes |
| BR-04 | R-05: Qdrant chunks accumulate on re-ingest | Confirmed: 4 distinct point ids after 2 ingests, no delete. | M | Yes |
| BR-05 | "Find Scholarships" repeats results; V-07 | Confirmed. The mocked profile (2 countries, 1 field, nationality) and 2 sources gave 8 steps and 16 rows for 4 distinct scholarships. In the live registry the country dimension is filtered by source country, but the other three dimensions add every discovery source, so each source's items repeat at least once per field, degree and nationality value. | M | Yes |
| BR-06 | MA-03: "no rules → Likely eligible" | **Refined.** It happens only when the scholarship has a known, future deadline. With no deadline the verdict is `unknown_requires_verification`. The test that locks "likely" in does so with a future deadline. | M | Yes, narrower than Part 1 said |
| BR-07 | §14/§17 #5: hard checks run only for rows the user added | Confirmed by the same run: the profile's own nationality and GPA are ignored unless a hard-constraint row exists (the `profile(None)` runs list only `deadline`). | M | Yes |
| BR-08 | A-05e: exclusions never change the verdict | Confirmed: `exclusions_triggered=['requires GRE']` was echoed and the result stayed `likely / possible`. | M | Yes |
| BR-09 | §15 #11 / section 7: LLM routes return 500 on a Gemini error | Not re-run (needs HTTP through the app). Traced: no route except documents catches `APIError`; `core/llm.py:33` raises it unchanged. | R | Yes (traced) |

---

### P2.6 Test count and what each test needs

**Why Part 1 said 761 and earlier runs said 1015+.** They count different things.

| Number | What it is | Where it comes from |
|---|---|---|
| 761 | Lines that start a test function (`def test_…`, sync or async, at any indent). | `grep -rhE "^\s*(async )?def test_" backend/tests`. The gap analysis said "~761 test functions"; Part 1 repeated it, and in section 17 wrongly wrote "761 tests pass" although it ran nothing. |
| **1,031** | Tests pytest actually collects. `@pytest.mark.parametrize` (78 uses) turns one function into many, adding 270. | `pytest --collect-only -q -p no:cacheprovider -m ""` → "1031 tests collected" |
| **1,026** | What a normal `pytest` runs: the 5 `smoke` tests are deselected by `addopts = "-m 'not smoke'"`. | `pytest --collect-only -q` → "1026/1031 tests collected (5 deselected)" |
| 1,015 | Tests that **passed** in the recorded full run (plus 5 deselected = 1,020). That run is older; 11 tests have been added since. | `backend/full_us8_b4.txt`: "1015 passed, 5 deselected … 1:33:39"; `docs/t-002-verification-log.md:24` |

Per folder (`def` count → collected). The big jump is `services` (173 → 394) because the verdict matrix and satisfaction matrices are parametrized.

| Folder | `def test_` | Collected | Folder | `def test_` | Collected |
|---|---|---|---|---|---|
| agents | 19 | 19 | scheduling | 12 | 13 |
| api | 112 | 117 | services | 173 | 394 |
| core | 42 | 46 | smoke | 5 | 5 |
| data | 81 | 95 | sources | 91 | 115 |
| infra | 13 | 13 | tools | 46 | 46 |
| integration | 54 | 54 | workflows | 102 | 103 |
| rag | 0 | 0 | top level (`tests/*.py`) | 11 | 11 |
| | | | **Total** | **761** | **1,031** |

**Which tests need what.** A script (a pytest plugin that reads each test's fixture closure) classified all 1,031. The database tests are those using `db_session_factory`, `client` or `authed_user` (398) plus those in 11 modules that import `SessionLocal` directly (up to 58 more; they also self-skip when no database is reachable).

| Folder | Collected | Need the database | Need neither DB nor network | Live network (smoke) |
|---|---|---|---|---|
| agents | 19 | 8 | 11 | 0 |
| api | 117 | 103 | 14 | 0 |
| core | 46 | 13 | 33 | 0 |
| data | 95 | 48 | 47 | 0 |
| infra | 13 | 0 | 13 | 0 |
| integration | 54 | 38 | 16 | 0 |
| scheduling | 13 | 13 | 0 | 0 |
| services | 394 | 51 | 343 | 0 |
| sources | 115 | 73 | 42 | 0 |
| tools | 46 | 4 | 42 | 0 |
| workflows | 103 | 98 | 5 | 0 |
| top level | 11 | 6 | 5 | 0 |
| smoke | 5 | 0 | 0 | 5 |
| **Total** | **1,031** | **455** | **571** | **5** |

- **Need live network:** the 5 smoke tests. Four in `tests/smoke/test_live_services.py` (Qdrant reachable, Qdrant query, Gemini generation, Gemini embedding) and one in `tests/smoke/test_discovery_listing_extraction_live.py` (real Gemini plus a database). They are deselected in CI and run only by `deep-ci.yml` (weekly or manual).
- **Need the database (455):** all of them read `DATABASE_URL` from `.env`. **Locally that is the real, shared Neon database**, not a throwaway one: `tests/conftest.py:1-5` says so, and `conftest.py:185-192` guards against "real pending candidates" being touched. Each test runs inside a transaction that is rolled back, so data should not remain, but they are live connections to your live database. In CI the same tests run against a Postgres container instead (`ci.yml`). With the database unreachable they **skip**, they do not fail (`conftest.py:50-62`), which is why "all green" can hide 455 skips.
- **Qdrant and Gemini inside the default suite are mocked** (for example `tests/data/test_qdrant_client.py` patches the client). Nothing in the default suite makes a real LLM or Qdrant call.
- **Neither (571):** these ran during this audit. I selected exactly those tests, pointed `DATABASE_URL` at a dead loopback address, gave dummy Gemini/Qdrant keys, and made every non-loopback socket raise. Result: **571 passed, 0 failed, 460 deselected (455 database + 5 smoke) in 20 s**, Python 3.12.11, repo left unchanged (`git status` clean). Plugin: `docs/audit-part2-scripts/nodb_plugin.py`. The 455 database tests and 5 smoke tests were **not** run, so "does the full suite pass right now" is still open (commands in P2.7).

---

### P2.7 Things the repo cannot tell me: read-only commands for you

Rules every command follows: it prints **no secret value**. For `.env` it prints only the file mode, owner, key **names**, and whether a key is set or empty. For the database it prints role flags, grants and aggregate counts (no rows of personal data, no connection string). None of them writes anything. Run them on the VM unless a line says otherwise. Adjust `REPO` and the container name (`sudo podman ps -a --format '{{.Names}}'`; `docs/vm-deployment-notes.md` says it is `scholarship-ai-assistant_backend_1`).

#### A. The VM's `.env` and the running container

```bash
REPO=~/scholarship-ai-assistant      # the checkout that holds docker-compose.podman.yml
cd "$REPO"

# VR-01  Mode and owner (expect 600 and your deploy user; anything readable by "others" is a finding)
stat -c '%a %U:%G %n' .env .env.example

# VR-02  Is .env kept out of git?  (first must print a .gitignore rule; second must say "did not match")
git check-ignore -v .env
git ls-files --error-unmatch .env 2>&1 | head -1

# VR-03  Which keys exist, names only
sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' .env | sort -u

# VR-04  Which keys are set versus empty, names only (no values)
awk -F= '/^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=/ { k=$1; sub(/^[[:space:]]*(export[[:space:]]+)?/,"",k); v=substr($0,index($0,"=")+1); gsub(/^["\047 ]+|["\047 ]+$/,"",v); print k, (v==""?"EMPTY":"set") }' .env | sort

# VR-05  Keys the code reads that .env lacks, and keys .env has that the example lacks
K() { sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' "$1" | sort -u; }
echo "--- in .env.example but missing from .env:";  comm -13 <(K .env) <(K .env.example)
echo "--- in .env but not documented in .env.example (expect GEMINI_API_KEY here; see P2.8):"; comm -23 <(K .env) <(K .env.example)

# VR-06  Any value still equal to the example's placeholder (prints key names only)
awk -F= 'NR==FNR { if ($1 ~ /^[A-Za-z_]/) ex[$1]=substr($0,index($0,"=")+1); next }
         ($1 in ex) && substr($0,index($0,"=")+1)==ex[$1] { print $1 " has the same value as .env.example" }' .env.example .env

# VR-07  The non-secret tuning values (this is the only command that prints values; none is a secret)
grep -E '^(MONITORING_INTERVAL_MINUTES|FETCH_LOG_RETENTION_DAYS|HEALTH_WINDOW_N|RATE_LIMIT_PER_MINUTE|MAX_ALERT_REMATCHES_PER_RUN|CANDIDATE_VALIDATION_RECHECK_DAYS|MAX_CANDIDATE_VALIDATIONS_PER_RUN)=' .env

# VR-08  What the RUNNING container sees: key names only, plus the interval (Blueprint says 360, repo default is 60)
sudo podman exec scholarship-ai-assistant_backend_1 env | cut -d= -f1 | sort
sudo podman exec scholarship-ai-assistant_backend_1 printenv MONITORING_INTERVAL_MINUTES

# VR-09  Exposure: only Caddy should listen publicly; the backend should be 127.0.0.1:8000
sudo ss -ltnp | awk 'NR==1 || /:(80|443|8000) /'
sudo stat -c '%a %U:%G %n' "$(sudo podman volume inspect scholarship-ai-assistant_backend_storage --format '{{.Mountpoint}}')"

# VR-10  Behaviour from your laptop (replace SITE; -k because of `tls internal`). Expect 404, 308, 401 or 403.
curl -skI  https://SITE/metrics | head -1
curl -sI   http://SITE/         | head -1
curl -sk -o /dev/null -w '%{http_code}\n' https://SITE/profile
```

#### B. Database role privileges and live data (read-only session)

The connection string is read into a shell variable and never printed. `BEGIN READ ONLY` makes the server refuse any write, and works through Neon's pooler.

```bash
cd "$REPO"
DBURL=$(grep -m1 '^DATABASE_URL=' .env | cut -d= -f2- | tr -d "\"'" | sed 's#^postgresql+psycopg://#postgresql://#')
psql "$DBURL" -X -v ON_ERROR_STOP=1 <<'SQL'
BEGIN READ ONLY;

\echo '--- VR-11 role the app connects as, and its flags'
SELECT current_user AS app_role, current_database() AS db;
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = current_user;
SELECT r.rolname AS member_of FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
 WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user);

\echo '--- VR-11 what that role can do (an owner with CREATE can drop or alter any table)'
SELECT has_database_privilege(current_user, current_database(), 'CREATE') AS can_create_schemas,
       has_schema_privilege(current_user, 'public', 'CREATE')            AS can_create_in_public;
SELECT tableowner, count(*) AS tables FROM pg_tables WHERE schemaname = 'public' GROUP BY 1;
SELECT privilege_type, count(DISTINCT table_name) AS tables FROM information_schema.role_table_grants
 WHERE grantee = current_user AND table_schema = 'public' GROUP BY 1 ORDER BY 1;
SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname !~ '^(pg_|cloud_admin|neon)' ORDER BY 1;
SELECT version_num AS alembic_head_in_db FROM alembic_version;   -- expect f2b8d4a6c1e3

\echo '--- VR-12 live data behind the gap-analysis and Blueprint claims (aggregates only)'
SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM profiles) AS profiles,
       (SELECT count(*) FROM requirements) AS requirement_rows;
SELECT count(*) AS scholarships, count(deadline) AS with_deadline,
       count(*) FILTER (WHERE verification_status::text ILIKE 'verified') AS verified FROM scholarships;
SELECT status::text AS source_status, count(*) FROM source_registry GROUP BY 1 ORDER BY 1;
-- monitoring cadence actually running: ~24 scheduled runs/day at 60 min, ~4 at 360 min
SELECT started_at::date AS day,
       count(*) FILTER (WHERE trigger::text ILIKE 'scheduled') AS scheduled,
       count(*) FILTER (WHERE trigger::text ILIKE 'manual')    AS manual
  FROM monitoring_runs GROUP BY 1 ORDER BY 1 DESC LIMIT 7;
-- EACEA "136 of 136 attempts yielded 0 grounded candidates" (Blueprint section 46, open item 4)
SELECT sr.domain, count(*) AS checks_30d,
       count(*) FILTER (WHERE l.items_found = 0)            AS zero_items,
       count(*) FILTER (WHERE l.status::text ILIKE 'fail')  AS failures
  FROM source_fetch_log l JOIN source_registry sr ON sr.id = l.source_id
 WHERE l.started_at > now() - interval '30 days' GROUP BY 1 ORDER BY 1;

ROLLBACK;
SQL
unset DBURL
```

How to read VR-11: if `app_role` owns every table (the usual Neon default) and `can_create_in_public` is true, the web process can drop or rewrite any table. That is a risk worth a decision (a separate migration role), not a bug. Part 1 §18 listed "DB role privileges" as unchecked; this gives you the way to close it.

#### C. "Do the tests pass right now?"

```bash
cd backend
# VR-13  What I ran: collection only (no tests executed, no database)
uv run pytest --collect-only -q -p no:cacheprovider | tail -3        # expect: 1026/1031 tests collected (5 deselected)

# VR-14  The 571 tests that need neither database nor network (what I ran; no external connection possible)
DATABASE_URL=postgresql+psycopg://nobody:nopass@127.0.0.1:1/none GEMINI_API_KEY=not-a-real-key \ # allow-secret
QDRANT_URL=http://127.0.0.1:1 QDRANT_API_KEY=x PYTHONPATH=../docs/audit-part2-scripts \
uv run pytest -q -p no:cacheprovider -p nodb_plugin -m "not smoke"       # expect: 571 passed, 460 deselected

# VR-15  The FULL suite exactly like CI, on a throw-away Postgres, so the shared Neon database is not touched.
# Environment variables beat .env, so this cannot reach Neon, Qdrant or Gemini.
docker run -d --name audit-pg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=scholarship_ci -p 5432:5432 postgres:16-alpine
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/scholarship_ci JWT_SECRET_KEY=dummy-ci-secret-not-real
export GEMINI_API_KEY=not-a-real-key QDRANT_URL=http://127.0.0.1:1 QDRANT_API_KEY=x
uv run alembic upgrade head && uv run pytest -q -p no:cacheprovider -rfE && uv run python -m evals.runner
docker rm -f audit-pg
```

```bash
# VR-16  What GitHub says (names and dates only): did CI run on main, and has deep-ci ever run?
gh run list --workflow ci.yml --branch main --limit 5
gh run list --workflow deep-ci.yml --limit 5     # empty output means it has never run (T138 says secrets were never added)
gh secret list                                   # prints names only; deep-ci needs QDRANT_URL, QDRANT_API_KEY, GEMINI_API_KEY
```

Not scriptable from here: Neon point-in-time restore (check the restore window in the Neon console; `docs/operations-hardening.md` has the drill) and whether alert email works (needs a real `SMTP_HOST`; VR-04 shows only whether it is set).

---

### P2.8 What Part 2 changes in Part 1, and what it adds

**Corrections to Part 1 (applied in place and marked "Part 2").**

1. Problem 15 of Part 1 said "761 tests pass". Wrong twice: 761 counts test functions (section 12 said so correctly, but did not explain the gap), and Part 1 ran no tests. Correct figures: 1,031 collected, 1,026 run by default, 571 of them offline and passing now, 455 needing the database, 5 live.
2. MA-03 and problems 2 and §15 #4 said a profile with no rules gets "Likely eligible". It does so only when the scholarship has a known future deadline; without a deadline the verdict is "Unknown, requires verification" (BR-06).
3. Six "(by reading)" items are now observed under mocks: W-01b, T-05, S-10, R-05, V-07 and the A-05e exclusion result (P2.5).

**New findings from Part 2.**

| ID | Finding | Where |
|---|---|---|
| N-1 | The match result always has empty `required_documents` and `remaining_actions`, so "what do I still need to do?" is never answered by the matcher. | J-26 |
| N-2 | A "new match" alert can never fire for a scholarship without a deadline, and listing-only records have none. The loop "monitoring finds a new scholarship and tells me" works only for records that already carry a future deadline. | AL-07, BR-06 |
| N-3 | When the alert budget (default 500 owner × change pairs) runs out, the rest are dropped for good; every re-match also writes a row and may spend a Gemini call. | AL-11, AL-13 |
| N-4 | Readiness says "AI can generate" for four document kinds (motivation letter, personal statement, study plan, research proposal) and Europass CV, but nothing generates them; task due dates are always empty; and the "verified" gate is satisfied by the LLM's own label. | RD-02, RD-05, RD-10 |
| N-5 | 71 of 140 tasks in `specs/001` are unticked although 67 are done; two ticked tasks in `specs/002` say the opposite of the code. The task lists cannot be used as a progress record. | P2.3 |
| N-6 | The local test run uses the shared live Neon database, and 455 of 1,031 tests silently skip when it is unreachable. A green run proves less than it appears to. | P2.6 |
| N-7 | The Blueprint's own §28 table gives itself 31 ticks; section P2.2 finds the code agrees with 11 of them (see section 16 for the totals). §2's table still says monitoring is "excluded". | B-60 to B-90, J-58 |
| N-8 | `.env.example` omits `GEMINI_API_KEY` (and `MAX_ALERT_REMATCHES_PER_RUN`), so a new deployment has no hint that the AI key is required. Retention stops silently when monitoring is switched off. | VR-05, RT-11 |
| N-9 | The Manager/Worker artifacts of §39-45 (`specs/handoffs/`, workstream briefs) do not exist; ADR-0009 and ADR-0008 carry no pointer to the ADRs that amend them. | B-152 to B-154, B-178, B-179 |
| N-10 | The weekly live-service workflow has, by the task list's own account, never run on GitHub (secrets not added). | T138, VR-16 |


---

## Part 3: PRD re-judged, eight files read line by line, every wrong task box

Part 3 was done on 2026-10-26 on the same branch. It is still read-only: no application code changed, no real Gemini call, no network call, no database connection. One helper script was added: `docs/audit-part3-scripts/part3_run.py`. It calls only pure functions (lifecycle rules, coverage maths, grounding gate, SOP/CV graph nodes, URL checks), makes every socket use raise, and printed "No network attempt occurred" (output in P3.4).

**Methods.** Same letters as Part 2: **R** read and traced, **M** ran the real function with nothing external (P3.4 has the output), **S** symbol or file scan, **T** test run. **Marks** are the same five (MATCHES, WEAKER, NAME ONLY, NOT REACHABLE, MISSING). A MATCHES row in P3.2 means I checked a claim the file makes and it holds.

---

### P3.1 The 92 PRD requirement IDs, re-judged

I went through all 92 rows of section 14 again with the Part 1 and Part 2 findings and the P3.2 findings below. **Nine IDs changed. The other 83 stay as they were.** Section 14 now shows the new verdict (marked "Part 3").

| ID | Was | Now | Why | Proof |
|---|---|---|---|---|
| FR-GEN-2 (never fabricate) | Met | **Partly met** | The grounding gate accepts a made-up grade. The list of "allowed facts" includes the scholarship's own requirement text, so "I have a GPA of 3.5." passes when the profile says 3.0 and only the requirement says 3.5. Document values are also labelled "verified" and the profile/document mismatch flags are never consulted. | CG-01, CG-04; observed in P3.4 (`cv_gen/graph.py:215-223,227`) |
| FR-GEN-1 (requirement-aware CV/SOP) | Partly met | **Not met** (in practice) | Europass is only a heading word (known). The scholarship's own SOP questions are read only from requirement keys such as `sop_question`, and nothing in the app creates a key like that, so every generated SOP is the generic "Personal Statement". It works only for a row inserted by hand. | SG-02; observed: key `essay` gives no questions (`sop_gen/graph.py:74,133`) |
| AC-3 (transcript satisfies a requirement; CV/SOP grounded only in real data) | Met | **Partly met** | "Grounded only in real data" is broken by the same gate hole. The live "satisfied" path was never shown live (task T130 says so). One document can satisfy only one requirement. | CG-01, DP-03, B-15 |
| FR-PLAN-2 (six-label checklist) | Met | **Partly met** | The labels exist but three are produced wrongly on common paths: `AI_CAN_GENERATE` for four documents nothing can generate, `NEEDS_OFFICIAL_VERIFICATION` passed when the AI labelled itself "verified", and unmatched keys falling into the catch-all. Due dates are always empty. | RD-02, RD-05, RD-09, RD-10 |
| FR-VERIFY-5 (nine lifecycle states) | Met | **Partly met** | Nine states exist, but the monitor can never move a stored record to `closed` (the flag that allows it is set nowhere and the extractor never says "closed") and never moves one to `expired` (nothing re-checks the deadline). Observed: a verified record with a deadline 30 days ago stays verified. | SM-01, SM-02; observed in P3.4 |
| NFR-REL-3 (monitoring finds new/changed scholarships, deadline changes, open/closed status, requirement changes; in-app and email alerts) | Met | **Partly met** | Open/closed status cannot be detected (SM-01) and requirement changes cannot (the monitor's own docstring says so). A "new match" alert cannot fire without a known future deadline. Alerts beyond the cap of 500 are dropped for good. Email uses STARTTLS only. | SM-01, AL-07, AL-13, AL-03 |
| NFR-REL-6 (accuracy is not traded for cost) | Met | **Partly met** | The alert re-match cap exists only to limit AI spend, and when it is reached the rest of the run's alerts are lost permanently with no visible sign. That is completeness traded for cost. | AL-13 (`alerts.py:322-330`, `config.py:44`) |
| KPI-1 (active/checked sources; coverage-gap count) | Met | **Partly met** | A source that was never fetched counts as healthy coverage. Observed: one active source with zero checks gives `countries_covered=['DE']` and `gaps=[]`. Three of the five seeded sources are never fetched by discovery or monitoring, so Germany, Hungary and Saudi Arabia show as covered. The gap count is therefore too low. | CO-01; observed in P3.4 (`coverage.py:106-107,125-135`) |
| NFR-4 (buildable in units with artifact hand-offs) | Met | **Partly met** | The hand-off artifacts the process sections describe (`specs/handoffs/`, workstream briefs, manager state) do not exist, and the task lists cannot be used as a progress record (P3.3). | B-152, B-153, B-154, N-5 |

**Direction of the changes:** eight Met to Partly met, one Partly met to Not met. Nothing moved up.

**New tally (92): Met 26, Partly met 50, Not met 16.** (Before: 34 / 43 / 15.)

**Looked at again and left unchanged** (so you can see I did not skip them): FR-ROUTE-1/2/3 stay Partly (built, tested, no caller, the same call Part 1 made); FR-AUTH-1 stays Met (logout is a no-op, but the PRD asks for no token revocation); FR-URL-1 stays Met (a verdict is produced; the missing "gaps" is counted under FR-MATCH-2); KPI-11 and FR-VERIFY-8 stay Not met (CO-02 shows the "verified" count is only a re-sighting count); NFR-REL-1 stays Partly (a freshness window exists, no seeded source sets one, so all use 180 days); NFR-REL-2 stays Partly (IG-01 confirms there is no backoff in ingestion); AC-5 and KPI-7 stay Met (the matrix and its CI gate are real).

---

### P3.2 Eight files read line by line

Each file was read from the first line to the last. Rows list what I found, plus a few MATCHES rows for claims the file makes that I checked and that hold. Line numbers are in the file named in the heading unless a path is given.

#### `workflows/ingestion/graph.py` (708 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| IG-01 | The docstring says classify and dedup are "retried per T072's bounded retry policy". The retry is called with `sleep=lambda _delay: None`, so there is no wait between attempts. For classify the retry cannot even start: `classify_candidate` catches every AI error inside itself and carries on, so the graph never sees a failure. | `graph.py:19-22,126`; `tools/classify.py:136-141` | R | WEAKER |
| IG-02 | `official_source_confirmed` is `False` at every call site, so the part of the workflow built for confirmed records never runs: the verify step can only return `newly_discovered`, `expired` (past deadline) or `closed` (only if the raw record says so), a new record is always stored `unverified`, and "official beats unofficial" conflict resolution never has an official side. | call sites `agents/discovery/agent.py:244`, `source_monitor/graph.py:215,286,305`; `graph.py:319-333,554`; `services/verification.py:44-49` | R | NOT REACHABLE |
| IG-03 | The required-field gate "at least one official source" is met by the registry's own label (`official_status == "official"`), not by any check of the record. A listing item never confirmed on its own page still passes. | `graph.py:382-387` | R | WEAKER |
| IG-04 | Storing a new record is two separate commits: the scholarship (via `scholarship_repo.create`, which commits) and then its source link. If the second fails, the stage logs an error but the scholarship stays with no source link. `list_for_source` finds records only through that link, so the next monitoring run sees the name as new and can store a duplicate. | `graph.py:616,629`; `scholarship_repo.py:30-34,62-72` | R | WEAKER |
| IG-05 | The "merge with an existing duplicate" path keeps only provenance and conflict rows. The new record's other values, requirements and funding are thrown away, and a resolved conflict is written as a field row without changing the real column (for example `deadline`). Each re-ingest also adds another source link; there is no unique index, and `list_for_source` needs `distinct` because of it. | `graph.py:461-502` against `557-561`; `scholarship_repo.py:63-65`; `migrations/versions/d501fadbb3f1_*:90-91` | R | WEAKER |
| IG-06 | Fuzzy duplicate search can only compare against rows with the same lower-cased name. `_db_backed_existing` loads exact-name rows only, and nobody passes `existing_candidates` (the discovery agent does not). So the similarity half of dedup has almost nothing to compare against in live use. | `graph.py:178-191,234-236`; `scholarship_repo.py:44-54`; `agents/discovery/agent.py:171-176` | R | WEAKER |
| IG-07 | The index step swallows every error with a bare `pass`: no log line, no log row. The module promises "never drop silently". (It also never runs for discovered records, known.) | `graph.py:641-650` against `18-30` | R | WEAKER |
| IG-08 | A nameless record is rejected only in the store step, after the classify step has already spent an AI call and dedup a database query. | `graph.py:167-175,358-362,451-453` | R | WEAKER |
| IG-09 | Store writes an OK fetch-log row per record with `items_found=1` and no run id. The monitor's baseline reader ignores them (it filters by URL), but retention's floor (c) "latest OK row with items found" will now protect one of these rows instead of the real extraction row. No harm found today (floor (a) keeps the last runs), but floor (c) no longer does its job. | `graph.py:498-501,631-634`; `source_monitor/graph.py:171`; `services/retention.py:70-75` | R | WEAKER |
| IG-10 | The monitoring update path keeps the old value as a `_prior:<field>` row before overwriting and writes nothing else. | `graph.py:407-440` | R | MATCHES |
| IG-11 | Every stage failure writes a `source_fetch_log` row and returns `error` / `error_stage`; later nodes skip. The "never silently absent" claim holds for every stage except index (IG-07). | `graph.py:112-137` | R | MATCHES |

#### `workflows/source_monitor/graph.py` (629 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| SM-01 | **The monitor can never close a record.** Closing by absence needs `extraction_rules.listing_complete = true`; that flag is read in one place and set nowhere (not in a seed, a route, code or a doc). The other route, an explicit `closing_status = "closed"`, is never produced because the extractor has no such field. Observed: an absent record gives no change. So "closed picked up" (Blueprint Phase 5 DoD, PRD NFR-REL-3) cannot happen. | `graph.py:176-191`; `seed_sources.yaml` (no `listing_complete`); a search for `listing_complete` finds only `graph.py:178,180`; observed in P3.4 | R, M | NOT REACHABLE |
| SM-02 | **The monitor never expires a stored record.** `evaluate_fetch_outcome` and `evaluate_freshness` never return `expired`, and the monitor calls `compute_lifecycle_status` only to decide whether to lift an existing `expired`. A stored record whose deadline passes stays `verified`. Observed. Only a new record that arrives with an already-past deadline is stored as `expired`. | `lifecycle.py:7-11,109`; `graph.py:206-219,246-249`; `services/verification.py:42`; observed in P3.4 | R, M | MISSING |
| SM-03 | **"Verified" is a re-sighting.** Every matched open listing item is marked `verified` on the same run (`mark_confirmed_by_monitoring`), so a record is `verified` on its second sighting, about an hour after it was found, with no visit to its own page. This is V-01 pinned to the exact lines. | `graph.py:315-320`; `scholarship_repo.py:91-105`; `lifecycle.py:113-115` | R | NAME ONLY |
| SM-04 | The docstring says no AI call is made in the workflow except the listing extraction. Every new item also goes through `run_ingestion`, which calls classify (about one AI call per item, BR-02). The first run on a source with many items therefore costs one extraction plus one classify call per item; the docstring does not say so. | `graph.py:1-9,281-287`; `ingestion/graph.py:167-175` | R | WEAKER |
| SM-05 | Each check reads the source's whole fetch log just to find the last item count. With 90-day retention and an hourly run that is thousands of rows per source per run, loaded to look at one. | `graph.py:166-173`; `source_repo.py:423-431` (no limit); `config.py:49` | R | WEAKER |
| SM-06 | A run in which every source failed is still stored as `completed`. `failed` is used only when an unhandled exception escapes; per-source failure shows only in `sources_failed`. | `graph.py:604-612` against `613-624` | R | WEAKER |
| SM-07 | A source whose lock is held is skipped with an info log only: no counter, no fetch-log row. | `graph.py:432-435,583-584` | R | WEAKER |
| SM-08 | A merged duplicate is reported as a new record: any scholarship returned by ingestion is added as a `"new"` event, but ingestion can return an existing row when it merges. Rare (needs a deterministic dedup hit), but it would send a false "new match" alert. | `graph.py:288-294`; `ingestion/graph.py:461-502` | R | WEAKER |
| SM-09 | One failed fetch (retries exhausted) moves every stored record of the source to `source_unavailable` at once, while the source itself stays `active` until 5 failed runs. Matches Blueprint §33 for records and ADR-0008 for health, but the two views disagree for several runs. | `graph.py:371-374`; `lifecycle.py:96-101`; `config.py:50` | R | MATCHES |
| SM-10 | A crash inside one source is caught, rolled back and recorded as a failed check; changes already stored still reach the alert step. | `graph.py:397-416,573-585` | R | MATCHES |
| SM-11 | Which sources are checked: all with `discovery_role=true` and `access_method=web`, every run. `update_frequency` is not read. (Known; now line-exact.) | `graph.py:569-570` | R | WEAKER |

#### `workflows/doc_pipeline/graph.py` (266 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| DP-01 | Two of the seven nodes do not do what their names say. `receive` returns an empty dict; `identify_type` only checks that the file name ends in `.pdf`. The document type is whatever the caller declared. | `graph.py:106-117` | R | NAME ONLY |
| DP-02 | When the AI returns unusable JSON for the document, the error is swallowed with no log and no flag. The row ends with no extracted data and no reason, the same as "the document had nothing in it". The module's own text says every outcome is recorded. | `graph.py:136-142` against `10-32` | R | WEAKER |
| DP-03 | A document can satisfy only one requirement. The loop stops at the first satisfied outcome and the column holds one id. A transcript that fits both a GPA and an academic requirement marks whichever comes first in an unordered relationship; the other stays "needs human review". | `graph.py:168,206-210` | R | WEAKER |
| DP-04 | When the AI provider is down or over quota, the upload is kept and an `extraction_unavailable` flag is written (the only route that does this). But there is no way to run the reading again; the message tells the user to upload the file again. | `graph.py:143-151,82-86`; no reprocess route in `api/documents.py` | R | WEAKER |
| DP-05 | A corrupt or non-PDF upload never loses the file; the failure reason is written on the row. Satisfaction is checked with no AI call. | `graph.py:192-193,212-219,172-179` | R | MATCHES |

#### `workflows/cv_gen/graph.py` (377 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| CG-01 | **The grounding gate can pass a made-up grade.** The pool of "allowed facts" includes the scholarship's requirements. A requirement "min_gpa = 3.5" becomes a fact the draft may repeat as the applicant's own. Observed: with the profile saying GPA 3.0, the claim "I have a GPA of 3.5." is accepted (matched to the requirement fact); the long form "BS in Physics from MIT with GPA 3.5/4.0" is rejected. The SOP prompt forces claims into the short "I have ..." form, so the SOP is exposed in the same way (it imports the same helpers). | `cv_gen/graph.py:215-223,227`; `sop_gen/graph.py:48-53,83-90,211`; `tools/ground_check.py:74-93`; observed in P3.4 | M | WEAKER |
| CG-02 | The docstring says "verified requirements". The filter is `value_status == "known"`, which the URL-match AI sets for itself (T-07). | `cv_gen/graph.py:8,219-222` | R | WEAKER |
| CG-03 | **An empty draft passes the gate.** Zero claims means zero gaps, so the run is not blocked, an empty "CV (standard)" or "SOP" file is stored with an empty source trace, and the API reports success. The docstrings call an empty trace "a bug". Same in SOP. Observed. | `cv_gen/graph.py:281-306,28-31`; `sop_gen/graph.py:242-270`; observed in P3.4 | M | WEAKER |
| CG-04 | Facts taken from uploaded documents are labelled "Document-verified" although they are AI-read values, and `inconsistency_flags` is never read. If the document GPA disagrees with the profile GPA, both facts go into the pool and either may be stated. | `cv_gen/graph.py:191-212` | R | WEAKER |
| CG-05 | Two facts with identical wording overwrite each other in the source map, so the trace names the later source; an unmatched claim is recorded as source `"unknown"` instead of failing. | `cv_gen/graph.py:288,302`; `sop_gen/graph.py:249,265` | R | WEAKER |
| CG-06 | One bad claim blocks the whole run (a gate, not a filter), and the draft is retried once on bad JSON. | `cv_gen/graph.py:98-118,304-306` | R | MATCHES |
| CG-07 | The number fix is real: whole GPAs print as `4.0` so a formatting difference cannot cause a false rejection. Observed `4 -> 4.0`. | `cv_gen/graph.py:137-149` | M | MATCHES |

#### `workflows/sop_gen/graph.py` (375 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| SG-01 | The scholarship's SOP questions are not enforced. Nothing checks that each question is answered, or that a claim's `question` is one of the questions asked; the heading printed is the model's own text. Observed: a claim under "A question nobody asked" is accepted while "Why us?" is unanswered. | `sop_gen/graph.py:242-270,319`; observed in P3.4 | M | WEAKER |
| SG-02 | The question source is never produced. Questions are read from requirement or field keys containing `sop_question`, `motivation_question`, `essay_question` or `personal_statement_question`. No extractor, seed or route in the app creates such a key. Observed: the key `essay` gives no question. So the "scholarship-specific SOP" behaviour is reachable only by a hand-inserted row. | `sop_gen/graph.py:74,122-143`; a search of `app/` finds the markers only in `sop_gen`; observed in P3.4 | R, M | NOT REACHABLE |
| SG-03 | The polish step contradicts itself. The instruction tells the model to join neighbouring claims into one paragraph, and also to "return exactly one claim per input claim". The code keeps the reply only when the count is unchanged, so a joined reply is discarded after up to two extra AI calls and the unpolished text is used. | `sop_gen/graph.py:103-117,181-191` | R | WEAKER |
| SG-04 | The output is a list of "I have ..." sentences by design (the prompt allows only that wording). It cannot hold a reason, a goal or a motivation, so the "SOP / motivation" document is a fact list. | `sop_gen/graph.py:83-90` | R | WEAKER |
| SG-05 | The polished text is re-checked by the same gate before it is rendered, and a polish that adds something ungrounded blocks the run. | `sop_gen/graph.py:288-307` | R | MATCHES |

#### `services/lifecycle.py` (129 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| LF-01 | Pure and deterministic: no database, no clock, no AI. It never returns `updated` or `newly_discovered`, as it says. | `lifecycle.py:1-12,127-129` | R | MATCHES |
| LF-02 | Only `stale` and `source_unavailable` can come from this module for a record that is not re-sighted. `closed` needs a signal that does not exist (SM-01), `expired` is never returned (SM-02). So of nine states, `closed` and `reopened` (which needs `closed`) cannot occur through monitoring, `expired` occurs only when a record arrives with a past deadline, and the other six can. | `lifecycle.py:93-124`; observed in P3.4 | M | WEAKER |
| LF-03 | A record that was never re-confirmed (`last_verified_at` empty) or is `newly_discovered` can never go stale. The per-source freshness window is honoured, but no seeded source sets one, so all use 180 days. | `lifecycle.py:36,79-90`; `seed_sources.yaml` (no `freshness_window_days`) | R | WEAKER |
| LF-04 | A record the source stops listing is never closed (SM-01), so it leaves `verified` only after 180 days of not being seen, by going `stale`. Until then it stays "verified". | `lifecycle.py:83-90`; `source_monitor/graph.py:176-184` | R | WEAKER |

#### `services/coverage.py` (190 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| CO-01 | **A source that was never fetched counts as healthy coverage.** Sources with no fetch rows are skipped (`continue`), so they are never in the failing set, and "healthy" means only "active and not failing". Observed: one active source with no logs gives `countries_covered=['DE']`, `sources_checked=0`, `gaps=[]`. DAAD, Stipendium Hungaricum and KAUST are `discovery_role=false`, so neither discovery nor monitoring ever fetches them, yet DE, HU and SA are reported as covered. `sources_checked` is honest; `countries_covered` and `gaps` are not. | `coverage.py:101-135`; `seed_sources.yaml:20,78,136`; observed in P3.4 | M | WEAKER |
| CO-02 | `verified_opportunities_count` counts rows with `verification_status = verified`, which is the re-sighting label (SM-03). The number looks like confirmed scholarships; it is "scholarships listed twice". | `coverage.py:189`; `scholarship_repo.py:109-119` | R | NAME ONLY |
| CO-03 | `claims_complete_coverage` stays `Literal[False]`; the failed rule follows the latest run; the read is bounded (latest row and latest run only). | `coverage.py:6-10,67-76,151-157` | R | MATCHES |

#### `services/candidate_validation.py` (215 lines)

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| CA-01 | The only protection against fetching internal addresses (`unsafe_url_reason`) is used here and nowhere else. `fetch_url` in `tools/web_fetch.py`, which URL match, discovery and monitoring use, has none. The fix for X-05 is already written in the repo; it is just not applied to the route that needs it. Observed: it blocks `127.0.0.1` and `169.254.169.254`. | `candidate_validation.py:83-108,148,159`; a search of `app/` finds `unsafe_url_reason` only in this file; `tools/web_fetch.py:26-33` | R, M | WEAKER |
| CA-02 | The host is looked up once for the check and again by the HTTP client at connect time (a rebinding gap), and redirects are followed before the final address is checked. The module admits the second point. | `candidate_validation.py:20-23,69` | R | WEAKER |
| CA-03 | The "looks official" hints know only `.gov`, `.edu` and `<gov/edu/ac>.<cc>`, and English affiliation words. Observed: `.gouv.fr`, `.go.jp`, `.gob.mx` and `bund.de` give no pattern, and German text with "Universitaet" and "Ministerium" gives no term, although three seeded sources are German, Hungarian and Saudi. | `candidate_validation.py:51-58,114-136`; observed in P3.4 | M | WEAKER |
| CA-04 | The docstring says the monitor trigger (T244) is "deferred". It is wired and runs every monitoring run. The text is out of date. | `candidate_validation.py:10-13`; `source_monitor/graph.py:497-521,603` | R | WEAKER |
| CA-05 | Advisory only: its single write is the validation row, the candidate is read and the transaction ended before any network or AI call, and failures are recorded in the row. | `candidate_validation.py:193-215` | R | MATCHES |

---

### P3.3 Task boxes that are ticked wrongly or left unticked wrongly

`tasks.md` was not edited. "Box" is what the file shows now; "Should be" is what the code supports. Part 2 (P2.3) gave the grouped count; this lists every ID.

**Totals: 83 wrong boxes of 326.** 13 ticked boxes overstate the work (A below), 67 unticked boxes are done (B), and 3 unticked boxes are partly done (C). The other 243 are right: 166 ticked boxes and 77 unticked ones (the 76 of feature 003 plus T065, which is not built).

#### A. Ticked, but the work is not as the box says (13)

| Task | Box | Should be | Why | Proof |
|---|---|---|---|---|
| T162 | [x] | not done | Says DAAD, Stipendium Hungaricum and KAUST seed with `discovery_role: true`. All three are `false`, on purpose. | `seed_sources.yaml:20,78,136`; `tests/services/test_source_registry_seed_002.py:38-43` |
| T168 | [x] | not done | Says flip `discovery_role` to true for the same three. Same proof; the tick records a change that was reversed. | same |
| T137 | [x] | decided against | Task text says "INVESTIGATED, REJECTED: not built". The tick records the decision, not an eval. | no such file in `backend/evals/cases/` |
| T075 | [x] | partly | `search` tool: the domain filter is real, the backend raises `NotImplementedError`, nothing calls it. | `tools/search.py:40` |
| T076 | [x] | partly | `api_connector` tool: wrapper only, placeholder transport, no provider onboarded. | `sources/connectors/api_connector.py:58` |
| T087 | [x] | partly | "extract → normalize → dedup → verify → classify": real order is normalize, classify, dedup; there is no extract step; verify never sees a confirmed source, so it cannot verify (IG-02). | `ingestion/graph.py:653-673`; IG-02 |
| T109 | [x] | partly | "format-specific (Europass)": Europass is a heading word and the file is `.txt`; the gate can pass a made-up grade (CG-01); an empty draft passes (CG-03). | `cv_gen/graph.py:321,215-223,290-306` |
| T110 | [x] | partly | "answers scholarship-specific questions": the questions are never created by the app (SG-02) and are not enforced (SG-01). | `sop_gen/graph.py:74,122-143,242-270` |
| T130 | [x] | partly | "Verified live end-to-end", while the task text lists three scenarios still not shown live. | `specs/001-scholarship-mvp/tasks.md:263` |
| T135 | [x] | partly | "Resolves US4 Scenario 1 (ranked results)": extraction is real, results are not ranked. | `api/discovery.py:37-60` |
| T138 | [x] | partly | Smoke tests and `deep-ci.yml` exist; per the task text the repo secrets were never added, so the workflow has never run. | `.github/workflows/deep-ci.yml`; `tests/smoke/*` |
| T178 | [x] | partly | "no LLM, no agent imports" is true of the module only: the extraction and every new item's classify call the AI. | `source_monitor/graph.py:442-453,281-287` (SM-04) |
| T179 | [x] | partly | Says changed "deadline/requirement/funding" are detected. Requirement changes cannot be (the extractor emits none; the module says so). | `source_monitor/graph.py:53-56`; `services/listing_diff.py:26` |

Boxes that are correctly ticked but have no live caller (not wrong; listed so nobody reads the tick as "usable"): T061 (orchestrator), T085 (`rerank`), T086 (`source_validate`).

#### B. Unticked, but the code is there (67)

For every row the box is `[ ]`, the code exists and does what the task says, so it should be `[x]`. "Named file" is the file the task itself names, checked to exist (T025 and T041 name a `{rev}` placeholder; the real files are `6e1035fe0cc6_create_profile_tables.py` and `603848a52d5b_*`; T140's proof is `official_fetch.py:93-109`). T020 is built but nothing calls it. T126 to T129 are built, but the orchestrator that T128 extends has no caller.

| Task | What the task says | Named file | Exists |
|---|---|---|---|
| T012 | [P] Shared `ValueStatus`/`Confidence`/`FieldValue` schema types (const | `backend/app/schemas/common.py` | exists |
| T013 | [P] Qdrant Cloud client wrapper | `backend/app/data/vectors/qdrant_client.py` | exists |
| T014 | [P] RAG document loaders | `backend/app/rag/loaders.py` | exists |
| T015 | [P] RAG chunking | `backend/app/rag/chunk.py` | exists |
| T016 | [P] Embedding tool wrapper | `backend/app/tools/embed.py` | exists |
| T017 | RAG embedding pipeline (ingests via T014/T015, embeds via T016) | `backend/app/rag/embed.py` | exists |
| T018 | RAG retrieval over Qdrant | `backend/app/rag/retrieve.py` | exists |
| T019 | [P] `ground_check` tool (used by Q&A now; reused by CV/SOP generation  | `backend/app/tools/ground_check.py` | exists |
| T020 | RAG grounding-check node | `backend/app/rag/grounding.py` | exists |
| T021 | [P] [US1] Contract test: `GET/PUT /profile`, `POST /profile/criteria`, | `backend/tests/api/test_profile_api.py` | exists |
| T022 | [P] [US1] Integration test covering spec.md US1 Acceptance Scenarios 1 | `backend/tests/integration/test_profile_flow.py` | exists |
| T023 | [P] [US1] Unit test: `missing_info` tracking + `profile_criteria.kind` | `backend/tests/services/test_profile_service.py` | exists |
| T024 | [US1] SQLAlchemy + Pydantic models for `profiles`, `education_records` | `backend/app/models/profile.py` | exists |
| T025 | [US1] Alembic migration for profile tables | `backend/migrations/versions/6e1035fe0cc6_create_profile_tables.py` | exists |
| T026 | [US1] Profile repository, `user_id`-scoped | `backend/app/data/repositories/profile_repo.py` | exists |
| T027 | [US1] Profile service — CRUD, criteria classify/reclassify, missing-in | `backend/app/services/profile.py` | exists |
| T028 | [US1] Profile request/response schemas | `backend/app/schemas/profile.py` | exists |
| T029 | [P] [US1] `extract_profile` tool — CV-import field suggestions, not au | `backend/app/tools/extract_profile.py` | exists |
| T030 | [US1] Profile API endpoints: `GET/PUT /profile`, `POST /profile/criter | `backend/app/api/profile.py` | exists |
| T031 | [US1] Wire profile router into the app | `backend/app/main.py` | exists |
| T032 | [P] [US2] Contract test: `POST /scholarships/match-url`, `POST /schola | `backend/tests/api/test_matching_api.py` | exists |
| T033 | [P] [US2] Integration test covering US2 Acceptance Scenarios 1–4 (full | `backend/tests/integration/test_url_match_flow.py` | exists |
| T034 | [US2] **Hard-constraint verdict-correctness test matrix** — fixed set  | `backend/tests/services/test_hard_constraints_matrix.py` | exists |
| T035 | [P] [US2] Guardrail-violation tests: Matching-agent output contradicti | `backend/tests/agents/test_matching_guardrails.py` | exists |
| T036 | [P] [US2] Unit test: ranking never reorders past a hard-constraint fai | `backend/tests/services/test_ranking.py` | exists |
| T037 | [P] [US2] SQLAlchemy + Pydantic models: `scholarships`, `universities` | `backend/app/models/scholarship.py` | exists |
| T038 | [P] [US2] SQLAlchemy + Pydantic models: `requirements` | `backend/app/models/requirement.py` | exists |
| T039 | [P] [US2] SQLAlchemy + Pydantic models: `funding_details` | `backend/app/models/funding.py` | exists |
| T040 | [P] [US2] SQLAlchemy + Pydantic models: `matches` | `backend/app/models/match.py` | exists |
| T041 | [US2] Alembic migration for scholarship/requirement/funding/match tables | `backend/migrations/versions/603848a52d5b_*` | exists |
| T042 | [P] [US2] Scholarship repository | `backend/app/data/repositories/scholarship_repo.py` | exists |
| T043 | [P] [US2] Match repository, immutable append-only writes | `backend/app/data/repositories/match_repo.py` | exists |
| T044 | [P] [US2] `web_fetch` tool — official-page fetch with explicit failure | `backend/app/tools/web_fetch.py` | exists |
| T045 | [P] [US2] `extract_requirements` tool — LLM-assisted extraction of req | `backend/app/tools/extract_requirements.py` | exists |
| T046 | [US2] `hard_constraints` service — deterministic GPA/degree/nationalit | `backend/app/services/hard_constraints.py` | exists |
| T047 | [US2] `ranking` service — deterministic ranking formula, agent supplie | `backend/app/services/ranking.py` | exists |
| T048 | [US2] `MatchVerdict`/`CriterionOutcome` schemas — fixed, schema-locked | `backend/app/schemas/match.py` | exists |
| T050 | [US2] Matching agent — guardrailed: schema-locked output, evidence-req | `backend/app/agents/matching/agent.py` | exists |
| T051 | [US2] URL-match API endpoint: `POST /scholarships/match-url` | `backend/app/api/url_match.py` | exists |
| T052 | [US2] Matching API endpoints: `POST /scholarships/{id}/match`, `GET /m | `backend/app/api/matching.py` | exists |
| T053 | [US2] Wire `url_match` + `matching` routers into the app | `backend/app/main.py` | exists |
| T054 | [P] [US3] Contract test: `POST /scholarships/{scholarship_id}/qa` | `backend/tests/api/test_qa_api.py` | exists |
| T055 | [P] [US3] Integration test covering US3 Acceptance Scenarios 1–4 (veri | `backend/tests/integration/test_qa_flow.py` | exists |
| T057 | [US3] `QaAnswer` schema (no persisted table — data-model.md §5) | `backend/app/schemas/qa.py` | exists |
| T059 | [US3] Q&A API endpoint: `POST /scholarships/{scholarship_id}/qa` | `backend/app/api/qa.py` | exists |
| T060 | [US3] Wire `qa` router into the app | `backend/app/main.py` | exists |
| T064 | [P] [US4] Contract test: `POST /discovery` | `backend/tests/api/test_discovery_api.py` | exists |
| T067 | [P] [US4] Unit test: `conflict_resolution` policy (official > recent > | `backend/tests/services/test_conflict_resolution.py` | exists |
| T068 | [P] [US4] Unit test: `coverage` counts/gaps computation, `claims_compl | `backend/tests/services/test_coverage.py` | exists |
| T077 | [P] [US4] `classify` tool — rules + LLM fallback for fuzzy classificat | `backend/app/tools/classify.py` | exists |
| T078 | [US4] `source_registry` service — registry CRUD, active-only gating | `backend/app/services/source_registry.py` | exists |
| T079 | [P] [US4] `normalize` service | `backend/app/services/normalize.py` | exists |
| T080 | [US4] `classify` service — dispatches to T077 | `backend/app/services/classify.py` | exists |
| T081 | [P] [US4] `dedup` service | `backend/app/services/dedup.py` | exists |
| T082 | [P] [US4] `verification` service — freshness window, `lifecycle_status | `backend/app/services/verification.py` | exists |
| T083 | [P] [US4] `conflict_resolution` service — official > recent > reliable | `backend/app/services/conflict_resolution.py` | exists |
| T114 | [P] [US6] Contract test: `POST /applications/{id}/assistant/next-step` | `backend/tests/api/test_application_assistant_api.py` | exists |
| T115 | [P] [US6] Integration test covering US6 Acceptance Scenarios 1–5 (ever | `backend/tests/integration/test_application_planning_flow.py` | exists |
| T116 | [P] [US6] Submission-approval scoping test: an approval for one `submi | `backend/tests/services/test_submission_approval_scope.py` | exists |
| T121 | [US6] `readiness` service — recomputes `readiness_label` from document | `backend/app/services/readiness.py` | exists |
| T122 | [US6] `app_plan` workflow — verified requirements → six-label checklis | `backend/app/workflows/app_plan/graph.py` | exists |
| T123 | [US6] `submit_prep` workflow — stops at `[APPROVAL GATE]`/`[FINAL APPR | `backend/app/workflows/submit_prep/graph.py` | exists |
| T126 | [US6] `GET /applications` (tracker) + `POST /applications/{id}/plan` e | `backend/app/api/application.py` | exists |
| T127 | [US6] Assistant + submission-approval endpoints: `POST /applications/{ | `backend/app/api/application.py` | exists |
| T128 | [US6] Extend orchestrator to route application-planning/assistant inte | `backend/app/orchestration/main_agent.py` | exists |
| T129 | [US6] Wire the remaining `application` endpoints into the app — minima | `backend/app/main.py` | exists |
| T140 | Stop fetching the homepage when a listing URL is set (fixed by 002 T164-T166) | `backend/app/sources/connectors/official_fetch.py:93-109` | exists |

#### C. Unticked, and only partly done (3)

| Task | Box | Should be | Why | Proof |
|---|---|---|---|---|
| T049 | [ ] | partly (leave unticked) | The workflow has no `verify` node: `fetch → extract → persist → ingest_rag → hard_constraints → matching → persist_match`. | `workflows/url_match/graph.py` |
| T058 | [ ] | partly (leave unticked) | Q&A uses retrieve and `ground_check`, not the T020 node, and cannot escalate to a live fetch. | `agents/research_qa/agent.py:8,11,52-54` |
| T124 | [ ] | partly (leave unticked) | The Application agent reports the next step; it does not draft, assemble or stage materials. | `agents/application/agent.py:1-5,63-108` |

(T065 is also unticked and is correctly unticked: its test file does not exist.)

---

### P3.4 What the helper script printed

`cd backend && PYTHONPATH=. .venv/Scripts/python ../docs/audit-part3-scripts/part3_run.py` with a dead loopback `DATABASE_URL` and dummy keys. Every socket use raises; none occurred.

```
=== LC: can the monitor ever close or expire a stored record? ===
absence may close (default source, 10 of 10 items, baseline 10): False
explicit closed signal from the extractor's field set ({}): False
  listed open: -> None
  absent, may_close False: -> None
  fetch failed, retries exhausted: -> source_unavailable
freshness on a record with a deadline 30 days in the past: None
=== CV: coverage counts a never-fetched source as healthy ===
countries_covered: ['DE'] sources_checked: 0 gaps: []
=== CVN: official-source signals for non-English official domains ===
  https://www.bmbf.gouv.fr/x []
  https://www.mext.go.jp/x []
  https://www.bund.de/x []
  https://www.ucl.ac.uk/x ['.ac.uk']
  https://www.sep.gob.mx/x []
affiliation terms matched on German text: []
unsafe_url_reason 127.0.0.1: host resolves to non-public address 127.0.0.1 | 169.254.169.254: host resolves to non-public address 169.254.169.254
=== GEN: empty drafts and invented SOP question headings pass the gate ===
cv ground_check, draft with zero claims: {'blocked': False, 'source_trace': []}
sop ground_check, draft with zero claims: {'blocked': False, 'source_trace': []}
sop ground_check, claim under an invented question while 'Why us?' is unanswered: False [{'claim': 'My Name is Ada Lovelace.', 'question': 'A question nobody asked', 'grounded_in': 'profile.name'}]
sop question derived from key 'sop_question': ['Why this programme?']
sop question derived from key 'essay': []
cv _fmt_decimal(4): 4.0
No network attempt occurred.
```

One more check for CG-01, run inline (not saved as a script): the same pure function `verify_claim_grounded`, evidence = one requirement fact plus one profile fact saying GPA 3.0:

```
'I have a GPA of 3.5.'                                  grounded=True   matched: Scholarship requirement (gpa): min_gpa = 3.5.
'I have a min_gpa of 3.5.'                              grounded=True   matched: Scholarship requirement (gpa): min_gpa = 3.5.
'I have a BS in Physics from MIT with GPA 3.5/4.0.'     grounded=False
```

**What Part 3 changes in Parts 1 and 2.** (1) Nine PRD verdicts move (P3.1). (2) B-22 ("closed picked up", Part 2: MATCHES) is wrong on "closed": SM-01 shows it cannot happen; it should read WEAKER. (3) J-37 (SOP workflow, Part 2: MATCHES) should read WEAKER (SG-01, SG-02, SG-04). (4) B-20 and B-117 stand. (5) X-05 now has a known fix already in the repo (CA-01). (6) Four task boxes move from "done as claimed" to "partly" (T087, T110, T178, T179).

---

## Part 4: every remaining file under `backend/app`, read line by line

Part 4 was done on 2026-10-26 on the same branch. It is still read-only: no application code changed, no real Gemini call, no network call, no database connection. One helper script was added: `docs/audit-part4-scripts/part4_run.py` (output saved as `part4_output.txt`). It calls only pure functions and in-memory fakes. Every non-loopback socket use raises; the last line of the output reads "Non-loopback network attempts: 0". Two loopback connects are Python's own event-loop socket pair, used only by check RQ.

**What was read.** Every file the earlier Section 18 listed as "read only where a claim passed through" (`dedup`, `classify`, `normalize`, `conflict_resolution`, `verification`, `listing_diff`, `requirement_satisfaction`, the `url_match`, `app_plan` and `submit_prep` workflows, the extraction tools), then every other file under `backend/app` that Parts 1 to 3 had not read line by line. That is **113 files, about 10,300 lines**. With the 12 files Parts 2 and 3 read line by line, that makes 125 non-empty files (14,111 lines). The other 30 files are empty `__init__.py` files. Nothing under `backend/app` is unread.

**Methods and marks.** Same as Parts 2 and 3: **R** read, **M** ran the real code with nothing external (the helper script), **S** symbol or file scan (`grep`). Marks: MATCHES, WEAKER, NAME ONLY, NOT REACHABLE, MISSING. A MATCHES row means I checked a claim the file makes and it holds. IDs start with `P4-`. "Known" means an earlier part already reported it; the row then adds the exact lines.

**Not run.** Anything that needs a database (rollback behaviour, duplicate-domain approval, concurrent submits) was read, not run. Rows say so.

---

### P4.1 The files Section 18 named

#### `services/dedup.py`, `services/classify.py`, `services/normalize.py`

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-DD-1 | The exact-match half needs name, university and intake all non-empty. The listing extractor emits none of university or intake, so for discovered records it cannot fire (other side of IG-06). | `dedup.py:48-53,73`; `extract_listing.py:47-65` | R | WEAKER |
| P4-DD-2 | The similarity half embeds the candidate and every existing row on every call, with no cache. Observed: 5 existing rows give 6 embedding calls for one candidate. | `dedup.py:86-90`; script DD | R, M | WEAKER |
| P4-DD-3 | The decision rule (exact match gives merge decision, vector near-match gives flag, never auto-merge) is as the docstring says. | `dedup.py:73-101` | R | MATCHES |
| P4-CS-1 | The classify service is a one-to-one reshaping of the tool's output; it does what it says. | `classify.py:33-50` | R | MATCHES |
| P4-NZ-1 | **`normalize_record` throws away `funding_status`.** It skips every key ending in `_status` or `_confidence` as a "sidecar", so the real field `funding_status` is never normalized. The store step then finds no normalized value and falls back to the classifier's guess (or UNKNOWN), so the funding the page stated is never stored for a new record. The monitor later compares the extractor's value to the stored one, so the two disagree and a "funding changed" event is possible with no real change. Observed: keys out of the sample record are `name, degree_level, country`. | `normalize.py:36-38`; `ingestion/graph.py:162,505-512,516`; `listing_diff.py:26`; script NZ | R, M | WEAKER |
| P4-NZ-2 | An explicit sidecar is honoured even when it contradicts the value (value empty with status `known`; value set with status `unknown`), against the module's own "absent is always unknown" promise. No live caller passes sidecars. Observed. | `normalize.py:4-5,52,62`; script NZ | R, M | WEAKER |

#### `services/conflict_resolution.py`, `services/verification.py`, `services/listing_diff.py`

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-CR-1 | Order official, then recency, then reliability; a tie gives `conflicting` with all values kept. Matches the docstring. | `conflict_resolution.py:47-94` | R | MATCHES |
| P4-CR-2 | Values are compared by `repr`, so `"3.5"` and `3.5` count as a conflict. In live use `is_official` is true only for stored rows already marked verified, and a new record is never official (IG-02), so "official wins" decides little. | `conflict_resolution.py:40`; `ingestion/graph.py:202,275` | R | WEAKER |
| P4-VF-1 | The rule "never `verified` without an official confirmation" holds inside this function. | `verification.py:44-48` | R | MATCHES |
| P4-VF-2 | Lines 50 to 63 (REOPENED, STALE, UPDATED, VERIFIED) run only when `official_source_confirmed` is true, which no live call passes (IG-02). The 180-day number is hard-coded there, separate from the per-source `freshness_window_days`. | `verification.py:17,50-63`; call sites in IG-02 | R | NOT REACHABLE |
| P4-VF-3 | The "Verified" a user sees is `verification_status`, set by `mark_confirmed_by_monitoring` on a re-sighting. It does not pass through this function's rule. | `scholarship_repo.py:91-106` | R | WEAKER |
| P4-LD-1 | The docstring honestly says requirement changes cannot be detected. | `listing_diff.py:8-11` | R | MATCHES |
| P4-LD-2 | Deadline parsing accepts only `YYYY-MM-DD`. A datetime-shaped reply is treated as "not stated", so a real deadline change is silently missed. Observed. | `listing_diff.py:35-43`; script LD | R, M | WEAKER |
| P4-LD-3 | The monitor diffs `deadline`, `funding_status`, `degree_level` taken from the listing, where only the name is checked against the page (P4-EL-1). An invented deadline on a real name becomes a "deadline changed" event and an alert. | `listing_diff.py:26,67-74`; `extract_listing.py:141` | R | WEAKER |

#### `services/requirement_satisfaction.py`

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-RS-1 | Only the document's GPA is converted to a 4.0 scale; the requirement is assumed to be 4-point although the comment says otherwise. Observed: document 8.5/10 against a requirement of 8.0 (10-point) gives `not_satisfied`. | `requirement_satisfaction.py:70-80`; script RS | R, M | WEAKER |
| P4-RS-2 | A text requirement ("3.0 or equivalent") raises `ValueError`. Nothing in this module or in `doc_pipeline._satisfy_check_node` catches it, so the upload endpoint returns 500. Observed. | `requirement_satisfaction.py:70`; `doc_pipeline/graph.py:179` | R, M | WEAKER |
| P4-RS-3 | The degree check is a two-way substring test and a miss is a hard `not_satisfied`, against the module's own "unknown must not collapse into not_satisfied". Observed: "Master's" against "Masters" is `not_satisfied`. A higher degree also fails a lower requirement. | `requirement_satisfaction.py:8-12,131-145`; script RS | R, M | WEAKER |
| P4-RS-4 | A test requirement matches only if its `key` equals the test name. The extraction prompt allows any key (P4-ER-2). Observed: key `min_ielts` gives `unknown`. A requirement with no minimum also gives `unknown` even when the document shows IELTS 7.0, because line 94 returns before the "no minimum" branch at 104-110, which is dead code. | `requirement_satisfaction.py:94-110`; script RS | R, M | WEAKER |
| P4-RS-5 | The `language` category, where the extraction prompt puts IELTS and TOEFL, has no evaluator and always gives `unknown`. Observed. | `requirement_satisfaction.py:148-152`; `extract_requirements.py:26` | R, M | WEAKER |
| P4-RS-6 | The profile-inconsistency check ignores the document's `gpa_scale`, compares institution by exact string ("MIT" against its full name is flagged), and flags a test retake as a mismatch. | `requirement_satisfaction.py:181-217` | R | WEAKER |
| P4-RS-7 | "No LLM anywhere in this module" holds; it imports only types. | `requirement_satisfaction.py:19-25` | R | MATCHES |

#### `workflows/url_match`, `workflows/app_plan`, `workflows/submit_prep`, `workflows/source_validate`

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-UM-1 | The extraction (an AI call) runs before the "already stored?" check, so a URL the system already holds still costs one AI call whose result is thrown away. | `url_match/graph.py:72,80-82` | R | WEAKER |
| P4-UM-2 | The user's pasted URL is stored as `official_scholarship_url` and returned as `official_source`, with no check that it is an official or registered domain. | `url_match/graph.py:92,202` | R | WEAKER |
| P4-UM-3 | A stored URL is never refreshed; a second submission returns the old row. A date that is not `YYYY-MM-DD` silently becomes "no deadline". | `url_match/graph.py:52-58,80-82` | R | WEAKER |
| P4-UM-4 | No error handling between fetch and persist except the fetch failure. An AI error, a bad JSON reply, or `name: null` reaches the endpoint as a bare 500 (no exception handler exists in the app, P4-MN-1). | `url_match/graph.py:61-73`; `api/url_match.py:31-36` | R, S | WEAKER |
| P4-UM-5 | The fetch-failure short-circuit, reported as a failure and never as "not eligible", holds. | `url_match/graph.py:61-65,197-198` | R | MATCHES |
| P4-AP-1 | "No LLM call", one entry point, and an atomic full replace of the tasks all hold. | `app_plan/graph.py:129-141`; `application_repo.py:93-108` | R | MATCHES |
| P4-AP-2 | The docstring says items sort by `(not mandatory, category, key, id)`; the code sorts by `description`, and the draft has no `key`. Still deterministic. | `app_plan/graph.py:17-20,71`; `readiness.py:189-200` | R | WEAKER |
| P4-SP-1 | The two gates and "no outbound send in this module" hold as written. | `submit_prep/graph.py:160-197` | R | MATCHES |
| P4-SP-2 | The fingerprint covers only document ids, types and checksums. Nothing about the scholarship, its requirements or deadline is included, so an approval stays valid after those change. Failed-parse upload rows are part of it and cannot be deleted (P4-DOC-3). | `submit_prep/graph.py:94-113` | R | WEAKER |
| P4-SP-3 | An empty package can be approved: the approval route checks nothing is attached, and an empty checklist skips "action required". Observed: the empty fingerprint is authorized and staging returns empty lists. | `api/application.py:139-149`; `agents/application/agent.py:67-85`; script SP | R, M | WEAKER |
| P4-SP-4 | The "staged, human-reviewable" output is ids and type names only; no file name, checksum or text, and no route serves a file (P4-DOC-4). | `submit_prep/graph.py:178-196` | R | WEAKER |
| P4-SP-5 | Approvals never expire and cannot be revoked; any past row for the same scope and fingerprint authorizes forever. | `application_repo.py:114-150`; `submission_approval.py:27-31` | R | WEAKER |
| P4-SV-1 | `source_validate` has no caller anywhere in `backend/app`. | grep `run_source_validate` | S | NOT REACHABLE |
| P4-SV-2 | The docstring says `approve_candidate_source` is reached only through the gate edge. The API route calls it directly, so the gate protects nothing live. The "gate" is an `approved` flag passed in; nothing pauses. | `source_validate/graph.py:14-16,60-63`; `api/sources.py:66` | R | NAME ONLY |
| P4-SV-3 | The "heuristics" are two booleans. Observed: none of the five seeded official sources counts as `trusted_tld`, while `https://evil.example@good.org/x` does (host is cut at the first `/` without removing user-info). The module says "type/reliability heuristics" but infers neither. | `source_validate/graph.py:2,50-57`; script SV | R, M | WEAKER |

#### Extraction tools

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-JU-1 | `extract_json_object` is annotated `-> dict` but returns whatever `json.loads` gives. A JSON list reaches callers that call `.get`. Observed (listing extraction). | `_json_utils.py:16-30`; script EL | R, M | WEAKER |
| P4-ER-1 | The model class and the `funding_details` table have three fields (`tuition_amount_or_pct`, `family_dependent_benefits`, `dependent_policy`) that the prompt's JSON shape never asks for, so the extractor can never fill them. | `extract_requirements.py:35-45,60-72`; `models/funding.py:18,24,26` | R | WEAKER |
| P4-ER-2 | `confidence: "verified"` is offered with no rule for when to use it and nothing checks the reply; `key` is an unconstrained string, so every key-matching check downstream depends on the model's naming. The status and confidence columns are plain strings with no constraint. | `extract_requirements.py:28,32,53-57`; `models/requirement.py:37-38` | R | WEAKER |
| P4-ER-3 | Page text is cut to 20,000 characters with no signal; the text starts with menus (`load_html` keeps all visible text). | `extract_requirements.py:91`; `rag/loaders.py:16-22` | R | WEAKER |
| P4-ER-4 | No retry or repair on a bad reply (unlike document extraction), and the reply is validated only by pydantic, so `name: null` raises. | `extract_requirements.py:93-94` | R | WEAKER |
| P4-EL-1 | Only the `name` is checked against the page. Observed: a real name with an invented deadline (2099-01-01), `fully_funded` and an invented URL are all accepted. | `extract_listing.py:141`; script EL | R, M | WEAKER |
| P4-EL-2 | `detail_url` cannot be read from the page: tag stripping removes every `href`. The repo says so itself. Observed: an anchor reduces to its text. Any URL returned is invented or visible text. | `extract_listing.py:105`; `detect_candidate_links.py:5-8`; script EL | R, M | WEAKER |
| P4-EL-3 | The model sees 20,000 characters but names are checked against the full text; entries past the cut are never seen and nothing says so. | `extract_listing.py:112,124,141` | R | WEAKER |
| P4-EL-4 | The name check is real: every word of 3 or more letters must appear (threshold 1.0). It is a bag of words, not a phrase. | `extract_listing.py:141`; `ground_check.py:20-22` | R | MATCHES |
| P4-DF-1 | Reject-and-retry for invalid JSON, with a typed error, works as the docstring says. | `extract_document_fields.py:61-80` | R | MATCHES |
| P4-DF-2 | The document text is cut to 20,000 characters, so the last page of a long transcript (often where the cumulative GPA is) can be dropped silently. | `extract_document_fields.py:66` | R | WEAKER |
| P4-DF-3 | Extracted values are never checked against the document text, unlike listing names. An invented GPA becomes the evidence behind a `satisfied`. One `gpa` and one `gpa_scale` per document; no range check. | `extract_document_fields.py:45-53,66-76` | R | WEAKER |
| P4-EP-1 | CV import returns suggestions only and never saves. | `api/profile.py:88-89`; `extract_profile.py:23` | R | MATCHES |
| P4-EP-2 | The whole CV is sent with no length cap, and a bad value (for example degree level "Master") raises a validation error as a bare 500. | `extract_profile.py:21-23` | R | WEAKER |

#### Other tools

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-CT-1 | Rules first, one AI call only for the unresolved dimensions, never `verified`. As documented. | `tools/classify.py:122-147` | R | MATCHES |
| P4-CT-2 | Country and field are plain substring matches. Observed: "Thousands" gives `usa`, "Duke" gives `uk`, "starts" gives field `arts`. First hit in list order wins. The result is used only when the listing left the field empty, but then it is stored as lower-case `usa`/`uk`. | `classify.py:66-79,91-95`; `ingestion/graph.py:505-512`; script CL | R, M | WEAKER |
| P4-CT-3 | No negation: "not a fully funded scholarship" gives `fully_funded`. Combined with P4-NZ-1, this guess is the stored funding for a new record. | `classify.py:47-53`; script CL | R, M | WEAKER |
| P4-CT-4 | Degree takes the first of PhD, MS, BS in list order. Observed: "bachelor's, master's and PhD" and "master's ... supervisor ... PhD" both give PhD. | `classify.py:41-45`; script CL | R, M | WEAKER |
| P4-CT-5 | AI fallback values are stored as `str(value)` with no check against the allowed lists. | `classify.py:143-145` | R | WEAKER |
| P4-GC-1 | The grounding check is word overlap: no negation, no order, words under 3 letters ignored. Observed in Q&A: "No.", "Yes", "3.0" can never be grounded. | `ground_check.py:20-22,49-68`; script QA | R, M | WEAKER |
| P4-GC-2 | `verify_claim_grounded` does what it says (every number must appear in the matched text), though in any context. | `ground_check.py:74-93` | R | MATCHES |
| P4-DL-1 | The link filters (own domain, known domains, denylist, keyword, 5 per fetch) work as documented. | `detect_candidate_links.py:109-161` | R | MATCHES |
| P4-DL-2 | The returned `domain` has `www.` removed but `url` keeps it, which breaks de-duplication downstream (P4-SR-4). A sub-domain of the page's own domain counts as a different site. | `detect_candidate_links.py:94-98,144,159` | R | WEAKER |
| P4-TW-1 | `api_connector`, `listing_fetch`, `official_fetch`, `embed` tool files are shape-only wrappers and match their docstrings. | the four files | R | MATCHES |
| P4-TW-2 | `tools/official_fetch.py` (`official_fetch_tool`) has no caller. | grep | S | NOT REACHABLE |
| P4-PD-1 | `parse_pdf` gives typed errors and flags an image-only PDF without erroring. Observed: an owner-password-only PDF parses; a user-password PDF raises `PdfParseError`. | `pdf_parse.py:29-53`; script PP | R, M | MATCHES |
| P4-RR-1 | `rerank` has no caller and is off by default. | grep | S | NOT REACHABLE |
| P4-SE-1 | `search_tool` has no caller; its default backend raises `NotImplementedError`; the domain match is an exact `netloc` compare. | `search.py:39-43,56`; grep | R, S | NOT REACHABLE |
| P4-WF-1 | Failure is always reported as a fetch failure, never as "not eligible". | `web_fetch.py:33-45` | R | MATCHES |
| P4-WF-2 | The whole response body is read into memory with no size cap and no content-type check; the 15 s limit is per step, not a total deadline. | `web_fetch.py:27-52` | R | WEAKER |
| P4-WF-3 | Redirects are followed with no re-check of where they land, so any address guard on the first URL would be bypassed. | `web_fetch.py:30` | R | WEAKER |
| P4-WF-4 | The User-Agent is a placeholder (`example.invalid`). The seeded user-agent text (contact `...local`) is read by no code. | `web_fetch.py:10`; `seed_sources.yaml:34` | R, S | WEAKER |

#### Connectors, retry policy, source repository

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-RT-1 | Capped exponential backoff (2 s, 4 s, max 8 s), as Blueprint §33 describes. | `retry_policy.py:40-69` | R | MATCHES |
| P4-RT-2 | Every error is retried, including a permanent 404, because no connector passes `is_retryable`. Observed: 3 attempts and sleeps of 2.0 and 4.0 seconds, then failure. | `official_fetch.py:171-174`; `api_connector.py:173-176`; script RP | R, M | WEAKER |
| P4-AC-1 | The default API client is a placeholder (`GET https://{domain}?q=`, no user agent, no redirects, one fixed shape). No seeded source uses it. A failure always marks the source failing. | `api_connector.py:126-151,214` | R | WEAKER |
| P4-OF-1 | The active-only gate runs before any network call. | `official_fetch.py:161-163,337-339` | R | MATCHES |
| P4-OF-2 | **An AI failure during discovery is invisible.** The fetch writes an OK row, then the extraction call is unguarded. If it fails (quota, missing key, bad reply) the exception leaves with no log row and no failing mark. Discovery catches it into `fetch_errors`, and the API drops `fetch_errors`. Coverage still sees the OK row. The agent docstring says every failure becomes a measured gap. | `official_fetch.py:178-189,420`; `discovery/agent.py:22-27,285-298`; `api/discovery.py:57` | R | WEAKER |
| P4-OF-3 | **The cross-run extraction cache is defeated by ingestion's own rows.** The cache looks at the newest OK row with `items_found` set. Ingestion writes such a row per record with `fetched_url` empty, after the extraction row, and the resolved path then discards a row whose URL does not match. Observed: control (extraction row only) gives `cached`, 0 fetches; with one ingestion row after it, 1 fetch and a fresh extraction. So each `POST /discovery` re-fetches and re-extracts every discovery source (one AI call each), though sources say weekly or annual. Only the in-run cache works. | `official_fetch.py:252-261,357-358`; `ingestion/graph.py:498-501,631-634`; script OF | R, M | WEAKER |
| P4-OF-4 | Candidate detection swallows every exception with no rollback, so a database error inside leaves the session unusable for the next log write. The "never affects extraction" claim holds only for non-database errors. | `official_fetch.py:308-309` | R | WEAKER |
| P4-OF-5 | The cache hit returns every provenance row ever linked to the source (duplicates, closed records, no limit). | `official_fetch.py:362-365`; `source_repo.py:435-444` | R | WEAKER |
| P4-OF-6 | The off-domain and redirect-to-homepage checks work as documented. | `official_fetch.py:396-416` | R | MATCHES |
| P4-SR-1 | Active-only reads for fetching and active-plus-failing for monitoring, as documented; the health streak logic matches its docstring. | `source_repo.py:47-155,323-382` | R | MATCHES |
| P4-SR-2 | `approve_candidate_source` takes every registry field from the caller and checks none against the candidate. The domain rule accepts `169.254.169.254`, `10.0.0.5` and `internal.corp`. Observed. A duplicate domain is a unique violation the route does not map (409 covers only review status), so it would be a 500 (read, not run). | `source_repo.py:216-248`; `schemas/source.py:24-36`; `api/sources.py:65-70`; script SC | R, M | WEAKER |
| P4-SR-3 | `get_active_sources(country=...)` is exact equality. Seeded countries are codes (DE, EU, HU, PK, SA); a profile's countries are free text. See P4-DA-2. | `source_repo.py:58-59`; `seed_sources.yaml`; `schemas/profile.py:136` | R | WEAKER |
| P4-SR-4 | **Candidate de-duplication fails for any link with `www.`.** The detector's `domain` has no `www.`, the stored `url` does, and the comparison uses the URL's host. Observed: a `www.` link creates a new pending row on each sighting (1, 1, 1 created); a link without `www.` is bumped (1, then 0). It runs on every fetch, including hourly monitoring. | `source_repo.py:447-451,490-498`; `detect_candidate_links.py:94-98,159`; script SR | R, M | WEAKER |
| P4-SR-5 | `mark_source_failing` sets FAILING unconditionally although its docstring says it only tightens; a source an operator disabled in the meantime becomes failing, and `reactivate_source` then makes failing active. Race window only. | `source_repo.py:272-284,305-316` | R | WEAKER |
| P4-SR-6 | Review times use naive local `datetime.now()` while everything else is UTC-aware. | `source_repo.py:244,264,493` | R | WEAKER |
| P4-SR-7 | `get_fetch_logs_for_source` has no limit and two callers load a source's whole history to find one row; `get_all_fetch_logs` has no caller. | `source_repo.py:76-79,423-432`; `official_fetch.py:258` | R, S | WEAKER |
| P4-SR-8 | Re-running the seed command overwrites every field of an existing seeded source including `status` (it is a field of the create schema), so a source the monitor marked failing or an operator disabled returns to active. I did not find the seed run on deploy. | `source_repo.py:178-186`; `schemas/source.py:52`; `source_registry_seed.py:34-42` | R, S | WEAKER |

#### Agents and orchestration

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-DA-1 | The guardrail (agent calls only `get_active_sources` and `get_source_by_id`) holds. | `discovery/agent.py:141,260` | R | MATCHES |
| P4-DA-2 | The country dimension finds nothing for natural input. Observed with the five seeded sources: "Germany" gives no step; "DE" gives none (DAAD has `discovery_role: false`); "PK" gives HEC. A nationality-only profile gets EACEA and HEC. | `discovery/agent.py:139-143`; `seed_sources.yaml`; script DA | R, M | WEAKER |
| P4-DA-3 | Repeats: a source reached by N plan steps returns the same cached list each time and `results.extend` adds it N times; no de-dup by scholarship id (the 4 to 16 rows). | `discovery/agent.py:214-215,300` | R | WEAKER |
| P4-DA-4 | The docstring says a per-step failure never raises. The broad catch leaves a failed database session unusable, and the closing `get_coverage_summary(db)` would then fail. Only monitoring code calls `rollback()` (grep); discovery, ingestion, url-match and the API routes never do. Read, not run. | `discovery/agent.py:285,311-313,337` | R, S | WEAKER |
| P4-DA-5 | Results are ids only (`scholarship_id`, `source_id`, `source_type`, `verification_status`) and `GET /scholarships/{id}` returns six fields. No route shows a scholarship's provider, country, funding, degree, requirements or official URL. | `schemas/discovery.py:49-55`; `schemas/scholarship.py:15-23`; `main.py:65-81` | R, S | MISSING |
| P4-QA-1 | The pipeline shape (retrieve, answer only from excerpts, deterministic label) is as documented. | `research_qa/agent.py:28-54` | R | MATCHES |
| P4-QA-2 | The labels rest on word overlap with one chunk. Observed: "No.", "Yes", "3.0" are always `unknown`; "minimum GPA required is 3.5" against a chunk saying 3.0 is `verified`. `Inferred` (30% overlap) still shows a source and `last_verified_at` (the ingest time). | `research_qa/agent.py:13,44-50,63`; script QA | R, M | WEAKER |
| P4-QA-3 | A missing Qdrant or Gemini key raises to the endpoint, although `llm.py` says callers must surface "unknown". The question has no length limit. Retrieval has no minimum score. | `core/llm.py:18-22`; `api/qa.py:14-24`; `rag/retrieve.py:17-28` | R | WEAKER |
| P4-MA-1 | Schema-locked output, evidence-required validators, hard-over-soft name guard, no HTTP import: as documented. | `matching/agent.py:98-99`; `schemas/match.py:66-85` | R | MATCHES |
| P4-MA-2 | Exclusions never reach the verdict; the two rule functions do not take them. Observed: an exclusion `needs GRE` leaves "eligible" unchanged. | `matching/agent.py:115-132`; script MA | R, M | WEAKER |
| P4-MA-3 | `POSSIBLY` can never be returned. Observed over 20 hard/soft combinations: only eligible, likely, not, unknown. PRD FR-MATCH-4 names five verdicts. | `matching/agent.py:130-132`; script MA | R, M | WEAKER |
| P4-MA-4 | "Eligible" depends on soft preferences. Observed: only the deadline checked plus one soft "met" is `eligible`; every hard check passed with no soft preferences is `likely`. | `matching/agent.py:115-132`; script MA | R, M | WEAKER |
| P4-MA-5 | The model's reply is not validated: `null` or non-object items raise; no retry. | `matching/agent.py:93-96` | R | WEAKER |
| P4-AG-1 | The assistant is rule-based, calls only `run_app_plan` and `run_submit_prep`, as stated. | `application/agent.py:63,85` | R | MATCHES |
| P4-AG-2 | It blocks on `AI_CAN_GENERATE`, a label given to documents nothing can generate (RD-02), so those users get "action required" for good. | `application/agent.py:47-49,67-83` | R | WEAKER |
| P4-OR-1 | The orchestrator has no caller outside its own file. | grep | S | NOT REACHABLE |
| P4-OR-2 | It refuses "write/draft/generate ... SOP/CV" as "not available yet", although generation routes exist. Observed. | `main_agent.py:166-168`; `api/generation.py`; script OR | R, M | WEAKER |
| P4-OR-3 | A question that contains a link gets the generic "I'm not sure" reply, with or without an active scholarship. Observed. | `main_agent.py:281,296`; script OR | R, M | WEAKER |
| P4-OR-4 | Routing is substring keywords ("match", "?", "can i"); one failing action aborts the whole reply. | `main_agent.py:170-174,364-399` | R | NAME ONLY |

#### Remaining services

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-HC-1 | GPA ignores scale. Observed: 3.4/4.0 against a need of 8.0 gives `fail`; 8.5/10 against 3.0 gives `pass`. Only the first GPA requirement is used. A fail forces "Not eligible". | `hard_constraints.py:53-73`; script HC | R, M | WEAKER |
| P4-HC-2 | Nationality is exact text. Observed: "Pakistan" against `["Pakistani"]` gives `fail`. | `hard_constraints.py:82-97`; script HC | R, M | WEAKER |
| P4-HC-3 | A scholarship level of `other` fails every target degree instead of `unknown`. Observed. | `hard_constraints.py:100-117`; script HC | R, M | WEAKER |
| P4-HC-4 | Tests: the requirement key must equal the test name. Observed: key `min_ielts_score` fails a student with IELTS 7.0; a student with no scores entered fails (not unknown); only the first mandatory test is checked, so an IELTS-or-TOEFL scholarship fails a TOEFL-only student. | `hard_constraints.py:120-164`; script HC | R, M | WEAKER |
| P4-HC-5 | The student's own criterion `operator` and `value` are never read. Observed: value 99 and value 1 give identical outcomes. A hard constraint only switches a dimension check on. | `hard_constraints.py:53,76,100,120`; script HC | R, M | WEAKER |
| P4-HC-6 | `_DIMENSION_TO_REQUIREMENT_CATEGORY` and `any_hard_constraint_failed` are unused; `language` has no evaluator (`unknown`, observed). | `hard_constraints.py:18-24,201`; grep | R, S, M | WEAKER |
| P4-HC-7 | The universal deadline check works as written. | `hard_constraints.py:31-50` | R | MATCHES |
| P4-MS-1 | The matching service repeats the url-match matching step (it says so) and appends a new `matches` row on every call. | `services/matching.py:1-6,29-46` | R | WEAKER |
| P4-MR-1 | **`GET /matches` lists every historical match, not the latest per scholarship.** The repo returns all rows and `rank_matches` only sorts. Observed: two rows for one scholarship (older eligible, newer not) list as eligible then not. The orchestrator list does the same. | `match_repo.py:20-22`; `api/matching.py:37-56`; `ranking.py:29-30`; script MA | R, M | WEAKER |
| P4-PS-1 | A new profile has no missing-information rows until the first `PUT /profile`; they are not computed at creation, after criteria changes, or after CV import. url-match and match read them, so a new user's first verdict lists nothing missing (FR-PROFILE-3). | `services/profile.py:53`; `profile_repo.py:24-29`; `url_match/graph.py:140` | R | WEAKER |
| P4-PS-2 | Scalar profile fields can be set but not cleared (`None` is skipped); a criterion update with a stale `id` silently creates a new criterion. | `profile_repo.py:43-45,71-75` | R | WEAKER |
| P4-RK-1 | Ranking sorts on two enum ranks and puts "not" last, as the docstring says; it has no deadline, funding or preference weight. | `ranking.py:10-30` | R | MATCHES |
| P4-SG-1 | `services/source_registry.py` is imported by nothing, although its docstring says dedup, verification, conflict resolution and ingestion use it. | grep | S | NOT REACHABLE |
| P4-SD-1 | All five seeded rows have `robots_checked: false`. EACEA and HEC have `discovery_role: true` and no note, while the other three carry the note "discovery stays false until the robots review". No code reads `robots_checked`, `allowed_path_prefixes`, `min_request_delay_seconds`, `max_pages_per_run` or the seeded `user_agent`. | `seed_sources.yaml`; grep over `backend/app` | R, S | WEAKER |
| P4-SA-1 | The approval check is an exact match on scope and fingerprint. | `submission_approval.py:27-31` | R | MATCHES |

---

### P4.2 API, core and the rest

| ID | Finding | Proof | Method | Mark |
|---|---|---|---|---|
| P4-AU-1 | Signup accepts any password including an empty one; a password over 72 bytes is silently cut (observed: a different tail still verifies); email case is not normalised (observed: `Ada@x.com` and `ada@x.com` differ). | `schemas/auth.py:7-9`; `core/security.py:14-19`; script AU | R, M | WEAKER |
| P4-AU-2 | Signup is check-then-insert (a race is a 500) and a 409 reveals which emails exist. | `api/auth.py:19-26` | R | WEAKER |
| P4-AU-3 | Tokens are HS256, fixed 60 minutes, no refresh or revocation; logout does nothing (its comment says so). `JWT_SECRET_KEY` has no strength check. | `core/security.py:8-9`; `api/auth.py:49-54`; `core/config.py:18` | R | WEAKER |
| P4-AU-4 | `get_current_user` does what it says. | `api/deps.py:14-36` | R | MATCHES |
| P4-PR-1 | `import_cv` is `async def` but runs a blocking PDF parse and a blocking Gemini call on the event loop. The repo's notes say one worker, so one import stalls every request for the model's latency. The upload is read whole with no size limit and a bad PDF is a 500. | `api/profile.py:20-24,82-89`; `core/metrics.py:12-13` | R | WEAKER |
| P4-DOC-1 | Upload reads the whole file into memory, takes the extension from the client's file name, allows any type and size, and stores before validating. A storage-then-database failure leaves an orphan file. | `api/documents.py:57-70`; `storage.py:49` | R | WEAKER |
| P4-DOC-2 | Path traversal in storage is guarded as documented. | `storage.py:70-83` | R | MATCHES |
| P4-DOC-3 | A failed-parse upload keeps its row (as intended) but there is no list, delete or replace route: the repo's delete function has no caller and no route uses `DELETE` anywhere. The failed row stays in the package fingerprint. | `api/documents.py:12-16`; `document_repo.py:76-81`; grep | R, S | WEAKER |
| P4-DOC-4 | No route serves any stored file (no file response anywhere). Uploads and generated CV/SOP cannot be downloaded; the response carries the internal `file_ref` and the claim list. Widens the Europass note in item 13. | `schemas/document.py:45-51`; grep | R, S | MISSING |
| P4-GEN-1 | The 409 "gaps" mapping for a blocked generation is as documented. | `api/generation.py:66-67,90-91` | R | MATCHES |
| P4-APP-1 | The same scholarship can be applied to many times (no uniqueness); `Application.status` is never written after creation, so it stays `in_progress`; tasks have no status or done flag, so the tracker (FR-TRACK-1) can show only recomputed labels; the list is N+1 queries with no `ORDER BY`. | `application_repo.py:21-39`; `models/application.py:59,79-96`; `api/application.py:65-66`; grep | R, S | MISSING |
| P4-APP-2 | The approval scope and notes have no length limit; only `final-submission` is ever consulted. | `schemas/plan.py:69-70`; `application/agent.py:39` | R | WEAKER |
| P4-SRC-1 | There is no `POST /sources`, as promised. | `api/sources.py:1-7` | R | MATCHES |
| P4-SRC-2 | Approve and reject are open to any signed-up user and the user table has no role column, so the operator-only rule (FR-DISC-8) cannot be applied without a migration. | `api/sources.py:59-94`; `models/user.py:11-18` | R | MISSING |
| P4-MON-1 | A manual run is guarded by an advisory lock, as documented. A run stuck as `running` in a live process blocks all later runs (no age limit) until a restart. | `monitoring_repo.py:31-40`; `main.py:43-48` | R | WEAKER |
| P4-MET-1 | `RETENTION_ROWS_DELETED_TOTAL` is defined and exported but incremented nowhere, so it is always 0. | `core/metrics.py:45-49`; grep | R, S | WEAKER |
| P4-MN-1 | The app has no exception handler, so every uncaught error is a bare 500. Interactive docs and `openapi.json` are on by default. | `main.py:57`; grep | R, S | WEAKER |
| P4-RQ-1 | The 500 from an unhandled error has no `X-Request-ID` (the outer error handler builds it outside this middleware), though the docstring says the id is echoed on the response. The access line is still logged. Observed. | `core/request_id.py:8,53-66`; script RQ | R, M | WEAKER |
| P4-RL-1 | The rate limiter matches its docstring (token bucket per client, bounded buckets, `/health` exempt) and the podman compose passes `--proxy-headers`. The limit is one flat 120 per minute for every route. | `core/rate_limit.py`; `docker-compose.podman.yml:43-44` | R | MATCHES |
| P4-LG-1 | Structured one-line JSON logging with context ids works as documented. | `core/logging.py` | R | MATCHES |
| P4-CFG-1 | Missing required settings fail fast with names. The default interval is 60 (known). | `core/config.py:83-91` | R | MATCHES |
| P4-LLM-1 | `generate_text` has no timeout, retry, quota handling or token cap; an empty reply returns `""` silently; the model id is hard-coded; embeddings go in one call with no batching. | `core/llm.py:11,33-57` | R | WEAKER |
| P4-DB-1 | Repositories scope user-owned data by `user_id` as documented. | `application_repo.py`, `document_repo.py`, `match_repo.py`, `alert_repo.py` | R | MATCHES |
| P4-SCR-1 | `get_by_official_url` uses `scalar_one_or_none` on a column with no unique constraint; two rows with one URL would break every later call for it. Not triggered today (the stored homepage differs from the normalised URL string). | `scholarship_repo.py:21-27`; `models/scholarship.py:96` | R | WEAKER |
| P4-QD-1 | Qdrant timeouts, auto-created collection and index: as documented. | `qdrant_client.py:29-74` | R | MATCHES |
| P4-QD-2 | Chunk ids are random and there is no delete or replace-by-scholarship function, so re-ingesting a page keeps old versions retrievable as "Verified" evidence (known, now line-exact). | `qdrant_client.py:76-91` | R | WEAKER |
| P4-RAG-1 | The chunker and loader are deterministic and as documented. | `rag/chunk.py`; `rag/loaders.py` | R | MATCHES |
| P4-RAG-2 | `check_grounding` and `load_plain_text` have no callers; Q&A does its own retrieve plus `ground_check`. | grep | S | NOT REACHABLE |
| P4-SCH-1 | The per-source advisory lock is as documented but only the monitor takes it; discovery and url-match ingestion do not. | `scheduling/locks.py`; grep | R, S | WEAKER |
| P4-MD-1 | `CoverageSummary.claims_complete_coverage` is pinned `Literal[False]`, and the verdict validators force "not" on a hard fail and require evidence. | `schemas/discovery.py:46`; `schemas/match.py:66-85` | R | MATCHES |
| P4-MD-2 | Profile numbers have no range check and `target_countries` is free text. | `schemas/profile.py:95-102,136` | R | WEAKER |

**Checked and found fine** (so you can see they were read): `api/alerts.py`, `api/coverage.py`, `api/health.py`, `api/scholarships.py`, the model files, `candidate_validation_repo.py`, `alert_repo.py`, `db.py`.

---

### P4.3 What the helper script printed

`cd backend && DATABASE_URL=postgresql://x:x@127.0.0.1:1/x JWT_SECRET_KEY=dummy GEMINI_API_KEY= QDRANT_URL= QDRANT_API_KEY= PYTHONPATH=. .venv/Scripts/python ../docs/audit-part4-scripts/part4_run.py`. The full output is in `docs/audit-part4-scripts/part4_output.txt`. Key lines:

```
classify: "not a fully funded scholarship" -> fully_funded | "Thousands..." -> country usa | "Duke..." -> uk | "starts" -> field arts
listing: invented deadline 2099-01-01, fully_funded and detail_url all kept | model list reply -> AttributeError
normalize keys out of {name, funding_status, degree_level, country, closing_status}: ['name','degree_level','country']
requirements: doc 8.5/10 vs 8.0 -> not_satisfied | "3.0 or equivalent" -> ValueError | "Master's" vs "Masters" -> not_satisfied | key min_ielts -> unknown
hard: 3.4/4.0 vs 8.0 -> fail | 8.5/10 vs 3.0 -> pass | Pakistan vs [Pakistani] -> fail | level other vs MS -> fail | key min_ielts_score -> fail
verdicts reachable: ['eligible','likely','not','unknown_requires_verification']   exclusion changes nothing   GET /matches order: eligible, not
Q&A: "No." / "Yes" / "3.0" -> unknown ; "required GPA is 3.5" (chunk says 3.0) -> verified
plan: Germany -> [] | DE -> [] | PK -> HEC | nationality only -> EACEA, HEC
cache: extraction row only -> ('cached', 0) | ingestion row after it -> ('ok', 1)
404 retry: 3 attempts, sleeps [2.0, 4.0]
www link: rows created 1, 1, 1 | link without www: 1, 0
domain 169.254.169.254, 10.0.0.5, internal.corp accepted; localhost rejected
signup: empty password accepted; 100-char password verifies on first 72 bytes; Ada@x.com != ada@x.com
PDF: owner-password-only parses; user-password raises PdfParseError
unhandled-error 500 has X-Request-ID: False (a normal 200 has it: True)
Non-loopback network attempts: 0.
```

**What Part 4 changes in Parts 1 to 3.** (1) Item 8's budget burn gets a mechanism: the discovery cache never hits once ingestion has run (P4-OF-3), and the classify AI call decides funding for every new record (P4-NZ-1). (2) Item 3's "Likely" and item 4's "exclusions ignored" are now line-exact (P4-MA-2, P4-MA-4), and `Possibly` is unreachable (P4-MA-3). (3) Item 7 gains a second route to fetching internal addresses: source approval accepts them (P4-SR-2). (4) Part 1's "Discovery: failure surfaces as a coverage gap" is wrong for an AI failure (P4-OF-2). (5) FR-MATCH-4 (five verdicts) stays Partly met; FR-DISC-8 and FR-TRACK-1 stay as they were (Not met and Partly met). No PRD verdict moved.

---

## 16. Count of each mark (Part 1, Part 2, Part 3 and Part 4)

Computed by script from the Mark column (the last cell) of every table row, not by hand. Row IDs with a letter suffix (for example A-02b) count as their own rows. Part 1 counts were made with `grep -o` on sections 1 to 13; Parts 2 and 3 by reading each row's last cell, so a mark word inside another cell is never counted. Part 3 moved two Part 2 rows (B-22 and J-37, MATCHES to WEAKER) and the Part 2 counts below include that.

**Part 1, sections 1 to 13** (unchanged):

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
| 9. Documents, CV/SOP, planning and tracking | 3 | 4 | 0 | 0 | 1 | 8 |
| 10. Matching (Blueprint §18; PRD FR-MATCH) | 2 | 6 | 0 | 0 | 2 | 10 |
| 11. Source registry, discovery, pipeline, verification, monitoring | 5 | 8 | 1 | 0 | 3 | 17 |
| 12. Testing and evals (Blueprint §25, §25.1) | 2 | 5 | 0 | 0 | 2 | 9 |
| 13. Frontend (Blueprint §5) | 0 | 0 | 0 | 0 | 3 | 3 |
| **Part 1 total** | **47** | **69** | **10** | **8** | **27** | **161** |

**Part 2** (UNCHECKED = could not be judged from the repo; counted on its own, see section P2.7):

| Part 2 section | MATCHES | WEAKER | NAME ONLY | NOT REACHABLE | MISSING | UNCHECKED | Total |
|---|---|---|---|---|---|---|---|
| P2.1 Journeys 3.1, 3.2, 3.3 and the narrative sentences | 9 | 24 | 1 | 2 | 10 | 0 | 46 |
| P2.2 Blueprint §22-24, §26-28, §36-46 | 48 | 63 | 10 | 1 | 22 | 4 | 148 |
| P2.4 `readiness`, `alerts`, `retention`, every branch | 27 | 12 | 0 | 0 | 0 | 0 | 39 |
| **Part 2 total** | **84** | **99** | **11** | **3** | **32** | **4** | **233** |

**Part 3** (P3.2, the eight files read line by line):

| File | MATCHES | WEAKER | NAME ONLY | NOT REACHABLE | MISSING | Total |
|---|---|---|---|---|---|---|
| `workflows/ingestion/graph.py` | 2 | 8 | 0 | 1 | 0 | 11 |
| `workflows/source_monitor/graph.py` | 2 | 6 | 1 | 1 | 1 | 11 |
| `workflows/doc_pipeline/graph.py` | 1 | 3 | 1 | 0 | 0 | 5 |
| `workflows/cv_gen/graph.py` | 2 | 5 | 0 | 0 | 0 | 7 |
| `workflows/sop_gen/graph.py` | 1 | 3 | 0 | 1 | 0 | 5 |
| `services/lifecycle.py` | 1 | 3 | 0 | 0 | 0 | 4 |
| `services/coverage.py` | 1 | 1 | 1 | 0 | 0 | 3 |
| `services/candidate_validation.py` | 1 | 4 | 0 | 0 | 0 | 5 |
| **Part 3 total** | **11** | **33** | **3** | **3** | **1** | **51** |

**Part 4** (P4.1 and P4.2, 150 rows; the table is the Mark column of every `P4-` row):

| Mark | Rows |
|---|---|
| MATCHES | 40 |
| WEAKER | 96 |
| NAME ONLY | 2 |
| NOT REACHABLE | 8 |
| MISSING | 4 |
| **Part 4 total** | **150** |

**All four parts together: 595 rows.**

| Mark | Part 1 | Part 2 | Part 3 | Part 4 | Together | Share |
|---|---|---|---|---|---|---|
| MATCHES | 47 | 84 | 11 | 40 | **182** | 31% |
| WEAKER | 69 | 99 | 33 | 96 | **297** | 50% |
| NAME ONLY | 10 | 11 | 3 | 2 | **26** | 4% |
| NOT REACHABLE | 8 | 3 | 3 | 8 | **22** | 4% |
| MISSING | 27 | 32 | 1 | 4 | **64** | 11% |
| UNCHECKED | 0 | 4 | 0 | 0 | **4** | 1% |
| **Total** | **161** | **233** | **51** | **150** | **595** | 101% (rounded) |

Part 4 is the widest part (113 files) and about two rows in five are MATCHES, because many small files do what they say; the WEAKER rows cluster in the extraction tools, the hard-constraint rules, the connectors and the API edges. Part 2 is kinder than Part 1 (36% MATCHES against 29%) because the planning and retention code it read line by line is solid, and because many scope labels correctly say "not yet". Part 3 read the code that carries the monitoring, ingestion and generation claims, and found the most gaps per row of any part (about one row in five is MATCHES, four in five are WEAKER or worse), which is what you would expect from reading the inside of the busiest files. Where the audit looked at what the Blueprint *promises*, the picture is the same in all three parts: about four to five rows in ten are weaker than written and about one in eight is missing.

**Task lists (P2.3 and P3.3), 326 tasks:** done as claimed 233, partly done 13, decided against and not built 1, not done 79 (76 of them are feature 003, which is not started by design; 2 are 002 tasks reversed by a later decision; 1 is the missing 001 test T065). **83 boxes are wrong** (13 ticked but overstated, 67 unticked but done, 3 unticked and partly done). 71 of 001's 140 boxes are unticked, and 67 of those are in fact done.

**Part 4 ran no test suite**, only the helper script (P4.3).

**Tests (P2.6):** 1,031 collected (not 761); 571 need neither database nor network and all 571 pass now; 455 need the database; 5 need live services. Not run: the 455 and the 5. Part 3 ran no test suite.

**PRD requirement IDs (section 14), 92 IDs, after the Part 3 re-judge:** Met 26, Partly met 50, Not met 16. (Parts 1 and 2 gave 34 / 43 / 15; P3.1 lists the nine that changed.)

---

## 17. The 15 biggest problems, in simple words

Part 4 changed the list again (see the Part 4 notes inside items 3, 4, 5, 7, 8, 10 and 11); the 15 items are unchanged in number. Part 3 changed the list before that. Two items were merged (the tests and the project papers; the alerts and the new monitoring gaps) to make room for one new item, now number 2. Every item says where a later part changed it.

1. **None of the "agents" is really an agent, and nothing can pause for a human.** The system promises five AI agents that plan, use tools and loop. In the code, two make one AI call each and three are ordinary functions. The OpenAI Agents SDK, LangChain and MCP are never used. Workflows are straight pipes with no saved progress and no real "stop and wait": the "approval gates" are a database lookup. The candidate-validation workflow and the orchestrator are built but nothing calls them. *(Part 2: the Phase 4 goal, "the agent walks the steps, asks for missing documents and gets approvals", is not met at all.)*

2. **The CV and SOP writer can put a grade in a document that the user never had. *(New in Part 3.)*** The checker that is meant to stop made-up facts also treats the scholarship's own requirements as facts about the student. Shown by running it: the profile says GPA 3.0, the requirement says 3.5, and "I have a GPA of 3.5." is accepted. An empty draft is also accepted and saved as a finished document. Values read by the AI from uploaded documents are called "verified", and a mismatch with the profile is ignored. The SOP's "scholarship-specific questions" are never created by anything in the app, so every SOP is a generic list of "I have ..." sentences.

3. **Matching can say "Likely eligible" while checking very little, and never says what is left to do.** It only checks the rules the user typed in as hard constraints, and only four kinds (GPA, nationality, degree, tests) plus the deadline. With no rules and a known future deadline it still says "Likely"; with no deadline it says "Unknown" (Part 2 corrected Part 1 here). A text value like "3.0 or equivalent" crashes it (reproduced). The result's list of required documents and remaining actions is always empty. *(Part 4:)* the checks themselves misfire: GPA scale is ignored, nationality is exact text ("Pakistan" fails "Pakistani"), a test requirement fails unless the model happened to name its key exactly "IELTS", and a student who has not entered test scores gets "fail". The verdict "Possibly" can never be returned, and "Eligible" is decided by soft preferences. `GET /matches` also lists every past match, not the latest. (P4-HC-1 to 5, P4-MA-3, P4-MA-4, P4-MR-1)

4. **"I don't want this" rules do nothing.** If a student says "exclude anything that needs GRE", the program collects the answer and then ignores it when deciding the verdict (reproduced: the exclusion is echoed and the verdict stays "likely"). *(Part 4:)* line-exact: the rule functions do not take exclusions at all (P4-MA-2). The student's own hard-constraint value is also never read (P4-HC-5).

5. **The word "Verified" is hollow.** No code ever opens a scholarship's own page to confirm it. A record becomes "verified" because the same list page showed it a second time, about an hour after it was found *(Part 3 pinned this to the lines)*, and the "verified opportunities" number on the coverage page is that same count. Every call that could confirm a record on its own page passes "not confirmed", so the confirmed-record code never runs. Discovered records carry the source's homepage as their "official URL" and no deadline. *(Part 4:)* the funding a page states is thrown away for new records because the normalizer drops any key ending `_status`; the classifier's guess is stored instead (P4-NZ-1).

6. **The AI marks its own homework on requirements.** When a user pastes a URL, the AI reads the page, writes down the requirements, and also labels each one "known" and "verified". Nothing checks that against the page. A wrong GPA or deadline can then produce a hard "Not eligible". *(Part 2:)* the application checklist's own "needs official verification" check is passed by that same label. *(Part 3:)* the CV and SOP writers use the same label as their test for a "verified requirement".

7. **Any signed-up user has operator powers, and the server can be turned against itself.** Anyone can approve new sources, start monitoring runs that spend the AI quota, and make the server fetch any web address, including internal ones on the VM, through the URL-match feature. Signup is open and there are no roles. *(Part 3: a check that blocks internal addresses is already written in the repo, but only candidate validation uses it.)* *(Part 4:)* source approval also accepts internal addresses such as 169.254.169.254 as a hostname and takes every trust field from the caller (P4-SR-2).

8. **The free AI allowance (about 20 calls a day) is unprotected.** There is no budget; every user action spends it; sorting each new scholarship costs about one call (measured under mocks: one per candidate); every alert re-check can spend one too; and when Gemini says "quota used up" almost every endpoint returns a raw server error instead of "try again later". Feature 003 would fix this and has not been started (76 of 76 tasks open). *(Part 4:)* the discovery cache meant to protect the quota never hits once ingestion has run, so each discovery click re-fetches and re-extracts every source (P4-OF-3); an AI failure during discovery leaves no trace (P4-OF-2).

9. **The tests and the project's own papers give more comfort than they should.** There are 1,031 tests, not 761. 455 of them need the database, and locally that is the real shared Neon database; when it is unreachable they skip silently instead of failing. The AI is mocked everywhere in the normal run, the scored checks cover three narrow cases and compare to fixed thresholds, there are no extraction, verification or discovery evals, and the RAG folder has no tests. Only the 571 offline tests were verified to pass. *(Merged from the old item 15:)* 83 of the 326 task boxes are wrong, including two ticked tasks that say the opposite of the code; the Blueprint's self-audit gives itself 31 ticks and the code agrees with 11; the hand-off folders the process sections describe do not exist; `.env.example` leaves out the AI key.

10. **Q&A is fragile.** It cannot go and check the official page when it is unsure. It only works for scholarships added by URL (discovered ones are never indexed), it depends on Qdrant, which the repo's own notes say was unreachable on 2026-10-22, and its "Verified" label uses a check the code itself says can miss number errors. Re-submitting a stored URL also piles up duplicate chunks (reproduced).

11. **Discovery does not really use the profile, repeats itself, and does not catch duplicates.** The profile only picks which of the five sources to visit. Results are unranked, not matched, and repeat: the same 4 scholarships came back as 16 rows in the mocked run. Duplicate detection needs name, university and intake all known, which listings never give, a suspected duplicate is flagged and then ignored, and *(Part 3)* the "similar name" search can only look at rows with exactly the same name. When a duplicate is merged, its new values and requirements are thrown away. *(Part 4:)* a country like "Germany" or "DE" produces no search step at all with the seeded sources (P4-DA-2); results are bare ids and no route shows a scholarship's details (P4-DA-5); a link with `www.` creates a new candidate-source row on every sighting (P4-SR-4).

12. **Source rules are written down but not obeyed.** The registry stores robots status, delays, allowed paths and a bot name, and the governance notes say not to fetch until robots are checked. No code reads any of those, and two sources are fetched on every run.

13. **The application helper stops short and sometimes promises what it cannot do.** It tells you the next step but does not ask for documents or show drafts for approval. The "Europass CV" is a text file with the word Europass in its heading, with no download route. Only a CV and an SOP can be generated, yet the checklist labels four other kinds of document "AI can generate". Task due dates are never filled in.

14. **No frontend, and long jobs block the request.** There is no UI at all. Only the monitoring run can be started and polled; discovery, generation, URL match and uploads make the caller wait.

15. **Monitoring and alerts miss their main cases. *(Merged and widened in Part 3.)*** The monitor can never mark a scholarship closed (the switch that allows it is set nowhere, and the page reader never reports "closed") and never marks one expired, so a scholarship whose deadline has passed stays "verified". A "new scholarship matches you" alert fires only if the scholarship has a known future deadline, and discovered records have none. If more than 500 owner-and-change pairs come up in one run, the rest are dropped for good. Every re-check writes a database row and may spend an AI call. The coverage page counts a source that was never fetched as covered: Germany, Hungary and Saudi Arabia show as covered although their three sources are never visited.

---

## 18. Where this audit stopped, and what it did not cover

**Part 4 finished everything you listed; I did not run out of context.**

| Item | What was done | How deep |
|---|---|---|
| 1. The files Section 18 named | `dedup`, `classify`, `normalize`, `conflict_resolution`, `verification`, `listing_diff`, `requirement_satisfaction`, the `url_match`, `app_plan`, `submit_prep` workflows and all extraction tools read from first line to last (P4.1) | Read; 30+ behaviours also shown by running the real pure code (P4.3) |
| 2. Every remaining file under `backend/app` | 113 files, about 10,300 lines, read from first line to last (P4.1, P4.2) | Read; 150 rows |

**Is any file under `backend/app` still unread? No.** Counting `.py` files and the one seed `.yaml`, there are 125 non-empty files (14,111 lines). 12 were read line by line in Parts 2 and 3 and the other 113 in Part 4. The only files not opened are the 30 empty `__init__.py` files (zero bytes). Files outside `backend/app` (tests, migrations, specs, Dockerfile, Caddyfile) are not part of that statement; tests and migrations were looked at only where a claim passed through them.

**Still not covered or not judged:**

- **Read, not run.** Anything that needs a database or a real AI reply: the missing `rollback()` after a database error (P4-DA-4), the unmapped duplicate-domain error on source approval (P4-SR-2), the concurrent-submit failure in P4-SCR-1, and the effect of the profile and match rows on a live database. These follow from the code but were not observed.
- **Not checked externally.** The AI library's limits on embedding batch size and the real per-day quota were not looked up, so P4-LLM-1 says only that nothing handles them.
- **Part 1 to 3 limits still stand:** Blueprint §4 to §21 and §29 to §35 were judged in Part 1 only; facts that need the VM, the database or GitHub (commands VR-01 to VR-16) and the four UNCHECKED rows; test results for the 455 database tests and 5 live tests.
- **The helper scripts** (Parts 2 to 4) use fakes. A fake can show what a function returns for an input, not what the live system holds; for example the discovery-cache result (P4-OF-3) shows the logic with one hand-made ingestion row, not a count from the real log.
