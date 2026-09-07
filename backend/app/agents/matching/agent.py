"""Matching agent — guardrailed (constitution Principle II, NON-NEGOTIABLE).

Guardrails enforced by this module, in depth:
  (a) Schema-locked output — every verdict is a frozen `MatchVerdict` (schemas/match.py);
      there is no free-text verdict path anywhere in this file.
  (b) Evidence-required — enforced by MatchVerdict's own validators; this agent never
      bypasses them (it always constructs the real MatchVerdict type, never a dict).
  (c) No-guess — any criterion this agent cannot resolve is emitted as `unknown`, and
      unresolved profile criteria are folded into `missing_information`, never invented.
  (d) Bounded tool access — THIS FILE MUST NEVER IMPORT app.tools.web_fetch, app.tools.search,
      or any HTTP client. (Enforced here by omission; verified statically by
      tests/agents/test_matching_guardrails.py, which asserts the fact by source inspection.)
  (e) Hard-constraint input is read-only — `hard_outcomes` passed into build_verdict()
      is never recomputed, re-derived, or mutated by this agent; it is echoed as-is into
      the final verdict, and any criterion here whose name collides with a hard_outcomes
      criterion is dropped before it can compete with the hard-constraint result.
  (f) Deterministic ranking stays in services/ranking.py — this agent never sorts results.
"""

from app.models.profile import CriterionKind
from app.schemas.match import (
    CriterionOutcome,
    CriterionResult,
    EligibilityVerdict,
    MatchStrength,
    MatchVerdict,
)
from app.tools._json_utils import extract_json_object

_SYSTEM_INSTRUCTION = """You evaluate a student's SOFT preferences and EXCLUSIONS against one
scholarship's structured details. You are NOT evaluating hard constraints (GPA, degree,
nationality, deadline, mandatory tests) — those are decided elsewhere and are not your job.
Respond with ONLY a JSON object (no markdown, no commentary) of this shape:
{
  "soft_preferences": [
    {"criterion": string, "result": one of "met", "unmet", "unknown", "evidence": string or null}
  ],
  "exclusions_triggered": [string]
}
Rules:
- If you cannot determine whether a preference is met from the scholarship data given, use
  "unknown" and evidence null. NEVER guess or invent a "met"/"unmet" result without evidence.
- Every "met" or "unmet" result MUST include a specific `evidence` string quoting or
  paraphrasing the scholarship data that supports it.
- Do not comment on GPA, degree level, nationality, deadlines, or mandatory tests — that is
  out of scope for you.
"""

_SOFT_KINDS = {CriterionKind.SOFT_PREFERENCE, CriterionKind.EXCLUSION}


def _soft_criteria_description(profile) -> str:
    lines = []
    for criterion in profile.criteria:
        if criterion.kind not in _SOFT_KINDS:
            continue
        dimension = criterion.dimension.value if hasattr(criterion.dimension, "value") else criterion.dimension
        kind = criterion.kind.value if hasattr(criterion.kind, "value") else criterion.kind
        lines.append(f"- [{kind}] {dimension} {criterion.operator} {criterion.value!r} (note: {criterion.note or ''})")
    return "\n".join(lines) if lines else "(no soft preferences or exclusions declared)"


def _scholarship_description(scholarship) -> str:
    return (
        f"Name: {scholarship.name}\n"
        f"Provider: {scholarship.provider}\n"
        f"Country: {scholarship.country}\n"
        f"Field: {scholarship.field}\n"
        f"Funding status: {scholarship.funding_status}\n"
        f"Notes: {scholarship.notes}\n"
        f"Conditions: {scholarship.conditions}\n"
        f"Exceptions: {scholarship.exceptions}\n"
    )


def produce_soft_preference_outcomes(
    profile, scholarship, *, hard_outcome_criteria: set[str]
) -> tuple[list[CriterionOutcome], list[str]]:
    """Calls the LLM to reason ONLY about soft preferences/exclusions. Any
    criterion name colliding with a hard-constraint criterion is dropped —
    the hard-constraint result always wins and is never re-derived here."""
    has_soft_criteria = any(c.kind in _SOFT_KINDS for c in profile.criteria)
    if not has_soft_criteria:
        return [], []

    from app.core.llm import generate_text

    prompt = (
        f"Scholarship:\n{_scholarship_description(scholarship)}\n\n"
        f"Student's soft preferences / exclusions:\n{_soft_criteria_description(profile)}"
    )
    raw = generate_text(prompt, system_instruction=_SYSTEM_INSTRUCTION)
    parsed = extract_json_object(raw)

    outcomes: list[CriterionOutcome] = []
    for item in parsed.get("soft_preferences", []):
        criterion_name = str(item.get("criterion", "")).strip()
        if not criterion_name or criterion_name in hard_outcome_criteria:
            continue  # guardrail (e): never let a soft outcome shadow a hard-constraint criterion

        result = str(item.get("result", "unknown")).lower()
        if result not in ("met", "unmet", "unknown"):
            result = "unknown"

        evidence = item.get("evidence")
        if result in ("met", "unmet") and not evidence:
            result = "unknown"  # guardrail (c)/(b): no evidence => no-guess, force unknown

        outcomes.append(CriterionOutcome(criterion=criterion_name, result=CriterionResult(result), evidence=evidence))

    exclusions_triggered = [str(e) for e in parsed.get("exclusions_triggered", [])]
    return outcomes, exclusions_triggered


def _derive_match_strength(hard_failed: bool, soft_outcomes: list[CriterionOutcome]) -> MatchStrength:
    if hard_failed:
        return MatchStrength.NOT
    met = sum(1 for o in soft_outcomes if o.result == CriterionResult.MET)
    total = sum(1 for o in soft_outcomes if o.result in (CriterionResult.MET, CriterionResult.UNMET))
    if total == 0:
        return MatchStrength.POSSIBLE
    return MatchStrength.STRONG if (met / total) >= 0.5 else MatchStrength.POSSIBLE


def _derive_eligibility_verdict(hard_outcomes: list[CriterionOutcome], strength: MatchStrength) -> EligibilityVerdict:
    if any(o.result == CriterionResult.FAIL for o in hard_outcomes):
        return EligibilityVerdict.NOT
    if any(o.result == CriterionResult.UNKNOWN for o in hard_outcomes):
        return EligibilityVerdict.UNKNOWN_REQUIRES_VERIFICATION
    if strength == MatchStrength.STRONG:
        return EligibilityVerdict.ELIGIBLE
    return EligibilityVerdict.LIKELY if strength == MatchStrength.POSSIBLE else EligibilityVerdict.POSSIBLY


def build_verdict(
    scholarship_id,
    hard_outcomes: list[CriterionOutcome],
    soft_outcomes: list[CriterionOutcome],
    exclusions_triggered: list[str],
    missing_information: list[str],
) -> MatchVerdict:
    """Deterministic assembly. `hard_outcomes` is read-only input from the
    hard_constraints service and is echoed verbatim — this function never
    recomputes or overrides it. match_strength/eligibility_verdict are
    computed by fixed rules, not chosen by the LLM."""
    hard_failed = any(o.result == CriterionResult.FAIL for o in hard_outcomes)
    strength = _derive_match_strength(hard_failed, soft_outcomes)
    verdict = _derive_eligibility_verdict(hard_outcomes, strength)

    matched = [o.criterion for o in [*hard_outcomes, *soft_outcomes] if o.result in (CriterionResult.PASS, CriterionResult.MET)]
    failed = [o.criterion for o in [*hard_outcomes, *soft_outcomes] if o.result in (CriterionResult.FAIL, CriterionResult.UNMET)]
    unverified = [o.criterion for o in [*hard_outcomes, *soft_outcomes] if o.result == CriterionResult.UNKNOWN]

    return MatchVerdict(
        scholarship_id=scholarship_id,
        eligibility_verdict=verdict,
        match_strength=strength,
        hard_constraints=hard_outcomes,
        soft_preferences=soft_outcomes,
        exclusions_triggered=exclusions_triggered,
        matched_criteria=matched,
        failed_criteria=failed,
        missing_information=missing_information,
        unverified_criteria=unverified,
        required_documents=[],
        remaining_actions=[],
        evidence=[o.evidence for o in [*hard_outcomes, *soft_outcomes] if o.evidence],
    )
