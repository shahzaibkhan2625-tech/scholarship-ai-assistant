# ADR-0003: Lexical Grounding Fails Safe Toward False Rejection, Not Fabrication

> **Scope**: Document decision clusters, not individual technology choices. Group related decisions that work together (e.g., "Frontend Stack" not separate ADRs for framework, styling, deployment).

- **Status:** Accepted
- **Date:** 2026-09-20
- **Feature:** 001-scholarship-mvp (Phase 3 Slice 3E — T110/T111/T112, sop_gen/cv_gen generation grounding)
- **Context:** `app/tools/ground_check.py` implements two grounding mechanisms used across three features: `ground_check()` (bag-of-words lexical overlap, US3/Q&A's only caller, `app/rag/grounding.py`) and `verify_claim_grounded()` (lexical overlap plus a numeric-literal consistency check, used by both `cv_gen` and `sop_gen`, T097/T109/T110). Both are bag-of-words comparisons — a claim is "grounded" when enough of its words overlap with some allowed-fact text above a threshold. Building `sop_gen` (T110) surfaced that this comparison is brittle against LLM paraphrase in freely-written prose: a claim that is fully supported by an allowed fact can still score below the overlap threshold purely because the drafting LLM rephrased it (added connective words, changed verb forms, referenced the applicant by name instead of restating the fact verbatim), producing a **false rejection** — a true, grounded claim reported as an information gap — rather than a false acceptance of a fabricated one. A separate, previously-known gap in the same file is that bare `ground_check()` (no numeric check) *does* pass numeric overstatements (e.g. "2 years of experience" grounding a claim of "5 years"), which is exactly why `verify_claim_grounded()` was introduced as a stricter layer for generation and why `ground_check()` itself now carries a docstring warning against new generation callers.

## Decision

Accept lexical/bag-of-words grounding (`verify_claim_grounded`, layered on `ground_check`) as the grounding mechanism for `cv_gen`/`sop_gen` in Phase 3, with two mitigations applied in Slice 3E rather than a mechanism change:

1. **Prompt tuning toward near-verbatim phrasing.** Both workflows' draft and polish system instructions (`_DRAFT_SYSTEM_INSTRUCTION`/`_POLISH_SYSTEM_INSTRUCTION` in `cv_gen/graph.py` and `sop_gen/graph.py`) constrain the LLM to restate allowed facts using a small set of fixed prefixes ("I have ", "I am ", "My ") and ban introducing verbs, adjectives, or nouns not present in the source fact — reducing (not eliminating) the paraphrase distance between a drafted claim and its supporting fact.
2. **`_fmt_decimal()` in `cv_gen/graph.py`.** A genuine formatting bug, not a grounding-algorithm change: Postgres `NUMERIC` round-trips a whole-number GPA/scale as `Decimal("4")`, dropping the trailing `.0`. Left alone, this reliably made the draft LLM "correct" `4` to `4.0`, which `verify_claim_grounded`'s numeric-literal check then rejected as an unsupported overstatement — a false rejection caused by a formatting mismatch, not a real hallucination. Always rendering one decimal place for whole-number facts removes this specific failure mode.

Both are documented as mitigations of a known limitation, not a fix to the underlying comparison method. No change to `ground_check()`'s own behavior or its Q&A caller (`app/rag/grounding.py`) was made or is proposed here — that caller and gap are recorded as a separate, already-known limitation (see `ground_check()`'s own docstring).

## Consequences

### Positive

- The failure direction is safe for this feature's core guarantee (spec.md SC-004: zero fabricated facts, FR-GEN-2). A bag-of-words mismatch can only cause the gate to under-trust a true claim and block generation with a reported gap — it cannot cause the gate to over-trust and pass a fabricated claim through as grounded. The worst outcome is a false 409, never invented content reaching a user.
- No new grounding dependency (embeddings, a second LLM call, an NLI model) was introduced under time pressure to chase a hard-to-fully-close gap; `verify_claim_grounded` remains a deterministic, non-LLM check per constitution Principle II ("harness discipline" — grounding must not be a second LLM call trusting itself).
- The mitigations (verbatim-phrasing prompts, `_fmt_decimal`) are cheap, already shipped, and improved reliability against a real LLM without touching the comparison algorithm shared with Q&A.

### Negative

- **Known limitation, unresolved:** lexical grounding remains brittle against paraphrase. A future draft/polish prompt change, a different LLM, or free-form prose the fixed-prefix template doesn't anticipate can still reintroduce false rejections — a real fact worded differently enough to fall under the overlap threshold. This is a ceiling on generation reliability, not a bug to "finish fixing" with more prompt tuning alone.
- The mitigations narrow specific instances (numeric formatting, verbatim-restatement prompts) but do not generalize — a differently-phrased numeric fact, or a prose style the template doesn't cover, can still trigger the same class of false rejection.
- `ground_check()`'s separate numeric-overstatement gap (bare lexical overlap has no numeric awareness, so it would pass "5 years" against a "2 years" fact) is documented but intentionally left unfixed on that function, since its only caller (`app/rag/grounding.py`, Q&A/US3) was out of scope for this decision and changing its behavior risked regressing Q&A's already-tested grounding contract.

## Alternatives Considered

- **Semantic-similarity grounding** (embedding cosine similarity between claim and fact, replacing or supplementing the bag-of-words check) — not chosen now. Would likely reduce false rejections from paraphrase, but introduces a new dependency (embedding model/API call), a new threshold to tune, and its own failure mode (semantically-similar-but-factually-different claims scoring high) that needs separate evaluation before it can be trusted as a hard gate. Deferred rather than designed under this slice's time pressure.
- **Entity/claim extraction before comparison** (extract structured (subject, predicate, value) triples from both the claim and the allowed facts, compare at that level instead of raw text overlap) — not chosen now. Likely the more robust long-term fix for both the paraphrase-brittleness and numeric-overstatement gaps at once, but requires its own extraction step (another LLM call or a dedicated parser) whose own correctness would need to be evaluated before it could safely gate fabrication — nontrivial scope beyond Slice 3E.
- **Fixing `ground_check()`'s numeric gap directly** — rejected for this ADR's scope: its only caller (Q&A, `app/rag/grounding.py`) has its own tested contract; changing shared behavior for generation's benefit risked an untested regression to US3. `verify_claim_grounded()` was introduced instead as an additive, generation-only layer (T097), leaving `ground_check()` and its caller untouched by deliberate decision.
- **Do nothing (ship without the two mitigations)** — rejected: `_fmt_decimal` fixes a reproducible, pure-formatting false rejection with no downside, and the verbatim-phrasing prompt constraints measurably improved observed grounding reliability against the real Gemini API during Slice 3E's own testing. Both were low-risk, already-in-hand improvements; declining them would have shipped a strictly worse gate for no benefit.

## References

- Feature Spec: `specs/001-scholarship-mvp/spec.md` (SC-004, FR-GEN-2)
- Implementation Plan: `specs/001-scholarship-mvp/plan.md`
- Related ADRs: [ADR-0001](0001-source-registry-governance-and-seed-sources.md), [ADR-0002](0002-ingestion-dedup-merge-does-not-reconcile-scalar-columns.md)
- Related code: `backend/app/tools/ground_check.py` (`ground_check`, `verify_claim_grounded`), `backend/app/workflows/cv_gen/graph.py` (`_fmt_decimal`, `_DRAFT_SYSTEM_INSTRUCTION`), `backend/app/workflows/sop_gen/graph.py` (`_DRAFT_SYSTEM_INSTRUCTION`, `_POLISH_SYSTEM_INSTRUCTION`), `backend/app/rag/grounding.py` (Q&A's `ground_check` caller, unchanged)
- Evaluator Evidence: `backend/evals/cases/generation_groundedness.py` (T096) — fixed-case scoring of `verify_claim_grounded` against grounded/fabricated/overstated/unsupported claims, threshold 1.0, wired into `backend/evals/runner.py` and the CI score-regression gate
