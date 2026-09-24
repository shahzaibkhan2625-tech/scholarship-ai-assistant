# T130 — Live Quickstart Verification Log

Real HTTP requests against the running stack (`docker compose up --build -d`, commit
`e64e001`), backed by the real Neon dev DB and a real (resumed) Qdrant Cloud cluster.
No mocking. Verification identity: `t130-verification+24628b03@t130-verify.dev`
(user_id `ac49e56b-a421-40ab-a050-5ce325e18f0b`).

## Scenario-by-scenario results

### US1 — Build a constraint-typed profile: **PASS (4/4)**

| Scenario | Command | Result |
|---|---|---|
| 1. Hard-constraint retrievable | `POST /profile/criteria` (nationality=Pakistani, hard_constraint) then `GET /profile` | PASS — returned in `criteria[]` with `kind: hard_constraint` |
| 3. Missing info surfaced | `GET /profile` with GRE unset | PASS — appears under `missing_info[]`, not omitted |
| 2. Partial update preserves other fields | `PUT /profile` with only `test_scores` | PASS — nationality/target fields from the earlier PUT survived |
| 4. Reclassify criterion in place | `POST /profile/criteria` (soft_preference) then re-`POST` same `id` (hard_constraint) | **INCONCLUSIVE — test contamination, not a product defect.** This profile was reused across several earlier debug-crash iterations of my own verification script, so 3 duplicate `field`-dimension criteria rows had already accumulated by the time this scenario ran. My script picked the *first* matching `field` criterion (already `hard_constraint` from an earlier iteration) instead of the one just created in this run, so the live HTTP call re-posted a no-op rather than proving a soft→hard transition. |

**Direct evidence for the underlying capability** (since the live HTTP proof above is compromised): `backend/app/data/repositories/profile_repo.py:48-78`, `upsert_criterion`:
```python
def upsert_criterion(
    db: Session,
    profile: Profile,
    *,
    criterion_id: uuid.UUID | None,
    dimension: str,
    operator: str,
    value: object,
    kind: str,
    weight: float | None,
    note: str | None,
) -> ProfileCriterion:
    """Reclassifying a criterion (changing `kind`) is an update to the same
    row, never a new row, so matching always reads the current classification
    (data-model.md `profile_criteria` validation rule)."""
    criterion: ProfileCriterion | None = None
    if criterion_id is not None:
        criterion = next((c for c in profile.criteria if c.id == criterion_id), None)

    if criterion is None:
        criterion = ProfileCriterion(profile_id=profile.id, dimension=dimension, operator=operator, value=value, kind=kind, weight=weight, note=note)
        db.add(criterion)
    else:
        criterion.dimension = dimension
        criterion.operator = operator
        criterion.value = value
        criterion.kind = kind
        criterion.weight = weight
        criterion.note = note
```
Given a `criterion_id`, this strictly matches by `id` and mutates the same row in place (the `else` branch) — it never creates a second row for an existing id. The docstring itself states this is a data-model validation rule. This is strong code-level evidence the capability works as specified; it is not, however, a substitute for a clean live HTTP proof, which this run did not produce.

### US2 — Match a known scholarship by URL

| Scenario | Result |
|---|---|
| 4. Unreachable URL → 422 `FetchFailure` | **PASS** |
| 1. `UrlMatchResult` shape (verdict + hard/soft arrays w/ evidence) | **PASS** (structural) — real response against `https://www.chevening.org/scholarships/` had `verdict.eligibility_verdict`, `verdict.match_strength`, `hard_constraints[]`/`soft_preferences[]` each carrying an `evidence` field. No clean (unmutated-profile) 200 was obtained in isolation — see the first-attempt 500 below — so this is verified from the same response that also carries the Scenario 2 attempt. |
| 2. Failing hard constraint → `eligibility_verdict: not`, `result: fail` | **INCONCLUSIVE — real-world data limitation, not a demonstrated defect.** Actual: `eligibility_verdict: "unknown_requires_verification"`, every `hard_constraints[].result: "unknown"` (including the deliberately-failing `nationality=Martian` criterion). Root cause: the live Chevening page does not state an explicit nationality restriction the extractor could compare against — so nothing to fail against. This is the correct behavior required by *Scenario 3* ("a criterion absent from the source page appears with `result: unknown`, never defaulted to pass/fail"), not a violation of Scenario 2. Scenario 2 was never actually exercised because this particular real page doesn't carry a comparable nationality fact. |

**First live attempt this run failed with a real 500** before succeeding on retry — see Bug #1 below.

### US3 — Ask grounded questions: **FAIL (0/3) — hard, reproducible bug**

All three real Q&A calls (stipend question, spousal-policy question, off-topic weather question) returned `500`. See Bug #2 below — this is not intermittent; it fails on every call until fixed.

### US4 — Discover scholarships from governed sources

| Scenario | Result |
|---|---|
| 2. Coverage summary mechanics | **PASS** — `sources_configured: 5`, `sources_checked: 5`, `sources_failed: 1`, `claims_complete_coverage: false` (structurally `Literal[False]`), gap correctly reported (`"DE / national_education: no active source configured"`). |
| Governance (`/sources` excludes non-active) | **PASS** — the one `status: failing` source (DAAD) was correctly absent from `GET /sources`; only the 4 active sources were returned. |
| 1. Ranked results across ≥2 source types | **BLOCKED — see investigation below. Not a transient issue; currently impossible to demonstrate with this registry.** `results: []` despite 5/5 sources successfully checked. |
| 3. Pending candidate source recorded | **BLOCKED, same root cause** — before/after snapshots of `GET /sources/candidates` were both empty; no code path in the live discovery flow currently records a `candidate_sources` row (confirmed by `grep`: no caller of `source_repo`'s candidate-creation function exists in `app/services/` or `app/agents/`). |
| 4/5. Fetch failure surfaced as gap / conflict surfacing | Fetch-failure path partially confirmed (the DE/national_education gap is reported), but conflict surfacing (Scenario 5) could not be exercised — no results were ever produced to conflict. |

#### Investigation: is the "0 results" gap a documented, deferred scope decision?

You asked me to check whether this was already logged the way Resolution note 4 (`workflows/source_monitor/` deliberately unbuilt) was — rather than assume. I searched `tasks.md` (all 4 Resolution notes, all phase checkpoints), `spec.md`, `data-model.md`, `research.md`, `plan.md`, the full `history/prompts/` tree, and checked for an `history/adr/` directory (**does not exist — zero ADRs have ever been created in this repo**).

**What I found documented:** nothing, anywhere in the spec-kit artifacts, describes this as a deferred decision. Specifically:
- `spec.md:142`, **FR-DISC-4**: *"Discovery MUST support extraction, normalization, deduplication, official-source verification, freshness tracking, and expired/closed handling of results..."* — no carve-out excluding web-type sources from extraction.
- `tasks.md`'s 4 Resolution notes (lines 27-30) cover: (1) table placement in Phase 1 vs 2, (2) Q&A has no persisted table, (3) omitted, (4) `source_monitor` deliberately unbuilt. **None mentions listing-page extraction or web-vs-API source handling.**
- `tasks.md:187`, Phase 2 Checkpoint: *"US4 independently functional; source-registry compliance + conflict-resolution tests green — spec.md SC-002/SC-003 demoable."* — no caveat about results being empty for web sources.

**What I found in the code** (`backend/app/agents/discovery/agent.py:23-34`) — this is the *only* place this gap is acknowledged, and it self-flags as an interpretation, not a logged decision:
```
**Interpretation note (flagged per task instructions, §30/§7.3 don't spell
this out literally):** `official_fetch`/web sources are still invoked here
(so registry gating, retries, and coverage/fetch-log accounting are
exercised for every source type, not just APIs) but their raw HTML is NOT
auto-parsed into multiple structured candidates in this slice — no
listing-page extraction tool exists yet (`extract_requirements`, T045, is
scoped to a single already-identified scholarship page for the url_match
flow, not a multi-result listing page). Only `api_connector`-sourced records
(already-structured dicts) are handed to `run_ingestion` here. Building a
listing-page extractor is left to a follow-up slice; until then, official/web
steps contribute to the query plan and to coverage/fetch-log accounting but
not to `results`.
```
The comment itself admits the blueprint "don't spell this out literally" — i.e. this was an implementation-time interpretation, never elevated to a Resolution note or ADR the way the `source_monitor` deferral was. It also references a "follow-up slice" that has no corresponding task anywhere in `tasks.md`.

**Conclusion — and why it matters:** all 5 currently-registered sources use `access_method: web` (confirmed by direct DB query: EACEA, Stipendium Hungaricum, KAUST, DAAD, HEC Pakistan — zero use `api`). Since web-sourced candidates never reach `results` by design of this undocumented interpretation, **discovery currently returns zero results for 100% of the registry, unconditionally** — this is not a corner case, it is the only case. Given Phase 2 has previously been marked complete/verified/deployed, and FR-DISC-4 makes no exception for this, **this should be logged as a real, un-deferred gap against Phase 2's completion status**, not folded in as "already known" — nothing in the project's own documentation supports treating it as already known or accepted.

### US5 — Documents, CV, SOP

| Scenario | Result |
|---|---|
| Upload + parse | **PASS** — `parsed_meta` correctly extracted GPA/degree/institution from a real PDF. |
| 2. GPA inconsistency flagged | **PASS** — profile GPA 3.2 vs. document GPA 3.9 correctly produced `inconsistency_flags: [{"type":"profile_mismatch","field":"gpa",...}]`. |
| Corrupt file → 422 | **PASS**. |
| 4/5. CV/SOP gap → 409, not invented | **PASS** — both generation calls correctly returned 409 citing the specific untraceable claim (the mismatched-GPA document's 3.9 figure), rather than fabricating content. |
| 1. Transcript marks a requirement satisfied | **See COVERAGE GAP below — this needs correction to the initial framing.** |

#### Coverage gap: US5 Scenario 1, live/manual verification specifically

You asked me to log this as a coverage gap requiring a different fixture, and to state plainly whether any test — automated or manual — has ever exercised it. I checked, and the premise needs one correction before I log it:

**This scenario IS covered by an automated test:** `backend/tests/integration/test_document_generation_flow.py::test_scenario_1_transcript_parsed_associated_and_requirement_satisfied` uploads a transcript against a scholarship seeded with a `RequirementCategory.GPA` requirement (mocked LLM extraction, `gpa=3.5`) and asserts `body["satisfies_requirement_id"] is not None`. So it is **not accurate** to say no test in this project — automated or manual — has ever exercised this end-to-end; the automated suite does, deterministically, with a synthetic fixture.

**What genuinely is a gap:** *this live verification run* never exercised the satisfied path, and the reason is precise, not vague. Requirement matching only fires for `DocumentType.TRANSCRIPT` against `RequirementCategory.GPA` or `RequirementCategory.ACADEMIC` (`backend/app/workflows/doc_pipeline/graph.py:59`), and only sets `satisfies_requirement_id` when the deterministic evaluator (`backend/app/services/requirement_satisfaction.py`) returns `SATISFIED` (`doc_pipeline/graph.py:176-180`, `break`s on the first `SATISFIED` outcome — `NOT_SATISFIED`/`UNKNOWN` both leave it `None`). Chevening's real extracted requirements had **no GPA-category requirement at all**, and its one ACADEMIC-category requirement (`degree_level`) almost certainly expects a Master's-level degree — while my test transcript declared `"BS Computer Science"` (undergraduate, and the same education record used throughout this run, not tailored to this comparison). `_evaluate_academic`'s substring match (`backend/app/services/requirement_satisfaction.py:122-140`) would correctly return `NOT_SATISFIED` or `UNKNOWN` for that mismatch — so `null` here is plausibly the deterministically-correct output for the actual inputs, not a bug.

**The real gap:** no live, non-mocked, real-LLM-extraction run in this project has ever demonstrated the SATISFIED path end-to-end. To close it, a follow-up live verification needs either (a) a real scholarship page whose extracted GPA/ACADEMIC requirement plausibly matches a realistic test transcript, or (b) a purpose-built test scholarship with a known, live-extractable GPA requirement, used specifically for this kind of manual/live verification (distinct from the mocked automated fixture, which already proves the mechanism works in isolation).

### US6 — Application Planning & Assistant: **PASS**

- Checklist items each carry exactly one `ReadinessLabel` (6/6 items, single label each).
- `/assistant/next-step` shape consistent with `/plan`'s own checklist items (`AssistantStepResult.result.blocking_tasks[0]` matches a `/plan` checklist entry's shape).
- Two `submission-approvals` calls with different `submission_scope` (`t130-verification-run-1`, `t130-verification-run-2`) both succeeded independently (`201`, distinct `id`s) — no reuse/carry-forward observed.

## Bugs found (server-side, verified via container logs — not fixed, out of scope for T130)

**Bug 1 — `QdrantClient` has no explicit `timeout`.** `backend/app/data/vectors/qdrant_client.py:29`, `QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)` passes no `timeout`, so it defaults to `None` → the underlying REST client falls back to httpx's own default (~5s). A bulk chunk-upsert during RAG ingestion legitimately needs longer than that, especially against a freshly-resumed free-tier cluster. Result: the first live `match-url` call this run 500'd with `qdrant_client.http.exceptions.ResponseHandlingException: timed out` even though the cluster was confirmed healthy; the identical call succeeded on retry.

**Bug 2 — Missing Qdrant payload index for `scholarship_id`, breaking Q&A 100% of the time.** `backend/app/data/vectors/qdrant_client.py:69`, `search_chunks`'s `client.query_points(..., query_filter=Filter(must=[FieldCondition(key="scholarship_id", ...)]))` filters on `scholarship_id`, but neither `ensure_collection` nor `upsert_chunks` ever calls `create_payload_index` for that field. Every live Q&A call throws:
```
qdrant_client.http.exceptions.UnexpectedResponse: Unexpected Response: 400 (Bad Request)
Raw response content:
b'{"status":{"error":"Bad request: Index required but not found for \\"scholarship_id\\" of one of the following types: [keyword, uuid]. Help: Create an index for this key or use a different filter."}, ...'
```
This is deterministic, not transient — it will reproduce on every Q&A call against any scholarship until a payload index is created for `scholarship_id`.

**Bug 3 (observed gap, logged for T131's review) — partial commit on a client-visible 500.** During the first live `match-url` attempt (before the Qdrant timeout above was understood), the LangGraph workflow had already committed a real `Scholarship` row (`c2eab1e9-3394-45d2-b376-d6117bf10b22`) plus 6 `requirements` rows and 1 `funding_details` row *before* the failing `_ingest_rag_node` ran. Earlier graph nodes commit independently rather than the whole workflow sharing one transaction/rollback boundary — a client-visible `500` does not guarantee nothing was persisted. Flagging for T131 (code-review checklist pass) to assess whether `url_match`'s ingestion nodes should share one transaction boundary.

## Cleanup

Executed via the app's own SQLAlchemy session (`backend/app/data/repositories/db.py`'s `engine`), relying on `Scholarship`'s declared `cascade="all, delete-orphan"` on `requirements`/`funding_details`/`fields` (confirmed by reading `backend/app/models/scholarship.py:119-127` before running) for the scholarship's children, and explicit `DELETE` queries for everything else — same shape as `conftest.py`'s `_delete_user_cascade`, extended to also cover the global scholarship-side rows this run created.

Order: `submission_approvals` → `tasks` → `generated_documents` → `application_documents` → `application` → `matches` (user-scoped) → `scholarship_sources` (explicit, 0 rows) → `scholarship` (ORM `session.delete()`, cascades to `requirements`/`funding_details`/`fields`) → orphan debug user (`027d7d31-...`, zero children) → verification user's profile tree (`profile_criteria`/`missing_info`/`education_records`/`test_scores`/`experience`/`profile`) → verification user. `source_fetch_log` was explicitly excluded throughout, per its append-only audit-record status.

| Table | Before | After |
|---|---:|---:|
| submission_approvals | 2 | 0 |
| tasks | 6 | 0 |
| generated_documents | 0 | 0 |
| application_documents | 3 *(2 successful uploads + 1 corrupt-file 422 attempt — its row is persisted durably before parsing, per `documents.py`'s own design, so it existed and needed cleanup too)* | 0 |
| applications | 1 | 0 |
| matches (verification user) | 2 | 0 |
| requirements | 6 | 0 |
| funding_details | 1 | 0 |
| scholarship_sources | 0 | 0 |
| scholarships | 1 | 0 |
| orphan debug user | 1 | 0 |
| verification user | 1 | 0 |
| profile_criteria | 10 *(accumulated across earlier debug-crash iterations reusing the same profile — see US1 Scenario 4 note above)* | 0 |
| missing_info | 1 | 0 |
| education_records | 1 | 0 |
| test_scores | 1 | 0 |
| experience | 0 | 0 |
| profiles | 1 | 0 |
| **source_fetch_log (NOT deleted — append-only audit record)** | **333** | **333 (unchanged)** |

All target tables verified at 0 rows post-cleanup via fresh (non-stale-ORM-object) queries. `source_fetch_log` confirmed unchanged, preserved as the true record that this verification run happened.

## Bugs 1+2 re-verified (post-fix, commit `32a47f5`)

Targeted re-verification against the freshly rebuilt server (confirmed up via `GET
/docs` → 200) — real HTTP requests, no mocking, same methodology as the original run
above. Not a full T130 re-run; scoped to exactly the two scenarios Bug 1 and Bug 2
broke. New, clearly-tagged identity used (`t130-reverify+9a84f182@t130-verify.dev`,
user_id `40868cc2-a5a3-40b8-a943-aafeee034eca`) — separate from the T130 verification
identity and its cleanup record above, which this run did not touch.

The original run's scholarship (`c2eab1e9-3394-45d2-b376-d6117bf10b22`) was confirmed
**not** resolvable in the Neon DB (`Scholarship` row absent — expected, since T130's own
cleanup deleted it; only its now-orphaned Qdrant points remained, which is what the
retroactive Bug 2 index fix was proven against separately). A fresh verification
scholarship was created via `match-url` against the same URL as the original run
(`https://www.chevening.org/scholarships/`), which doubles as the Bug 1 regression
check.

### US2 — `match-url` first-try success (Bug 1 regression check)

| Call | Result |
|---|---|
| `POST /scholarships/match-url` (first attempt, no retry) | **PASS** — `200`, scholarship_id `1f5b9b11-e962-41ad-8f3a-ecce01960b30`, 25.09s elapsed. Previously 500'd on first attempt with `qdrant_client.http.exceptions.ResponseHandlingException: timed out` (Bug 1); 25s exceeds the old unset-timeout default (~5s) that caused that failure, so this elapsed time is itself evidence the higher timeout was both necessary and is now in effect, not that the operation happened to be fast this time. |

### US3 — Q&A (Bug 2 regression check)

| Question | Status | Confidence | Result |
|---|---|---|---|
| "What is the stipend?" | `200` | `unknown` | **PASS** (Bug 2) — no 500. See content note below re: confidence label. |
| "What is the policy on bringing a spouse or dependents?" | `200` | `unknown` | **PASS** (Bug 2 + matches quickstart Scenario 2's expected `confidence: unknown` for a fact the source never states) |
| "What's the weather like today?" (off-topic) | `200` | `unknown` | **PASS** (Bug 2) — endpoint no longer crashes; this call hit `/scholarships/{id}/qa` directly, bypassing Main Agent routing, so it doesn't exercise Scenario 4's "should decline/redirect" routing behavior — that's a Main Agent concern, out of scope for this Bug 1/2 check. |

All three previously failed with a hard `500` on every call (100% reproduction rate,
per the original log). All three now return `200`. **Bug 2 is fixed.**

**Content-level note, not a defect:** all three answers came back `confidence: unknown`
with the generic "could not confirm" text, rather than Scenario 1's expected
`confidence: verified` for the stipend question. Diagnosed directly (read-only) against
Qdrant before concluding this: 7 chunks are genuinely ingested and indexed for this
scholarship, and `retrieve()` returns 5 real, plausibly-relevant chunks for the stipend
query (scores 0.47–0.55) — this is not an empty-retrieval symptom of Bug 1/2 recurring.
The retrieved chunks are drawn from `chevening.org/scholarships/`, a general
overview/FAQ landing page (country lists, high-level program description) that does not
itself state a stipend figure or a spousal-accompaniment policy — the same page the
original T130 run already found lacks explicit criteria facts (US2 Scenario 2's
nationality finding, same URL). `unknown` here is the deterministically-correct,
never-guess output for this page's actual content (constitution Principle I), not a
new bug. A live demonstration of the `confidence: verified` path would need a page that
actually states a stipend figure — out of scope for this Bug 1/2 check.

### Cleanup

Same ORM-cascade approach as the original run. Rows created by this re-verification
only (T130's identity/rows untouched):

| Table | Before | After |
|---|---:|---:|
| users | 1 | 0 |
| profiles | 1 | 0 |
| matches | 1 | 0 |
| scholarships | 1 | 0 |
| requirements | 5 | 0 |
| funding_details | 1 | 0 |

All confirmed at 0 via fresh post-cleanup queries, plus explicit existence checks on
the exact `user_id`/`scholarship_id` this run created and cascade checks on their
`requirements`/`funding_details` rows.

**Conclusion: Bug 1 and Bug 2 are both confirmed fixed, end-to-end, against the live
stack.** `tasks.md` not yet updated, per instruction — pending separately.

