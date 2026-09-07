"""Guardrail-violation tests (constitution Principle II, Blueprint §22 Phase
1): these MUST fail the build the moment a guardrail is violated — they are
not warnings. Each test deliberately constructs a violating output and
asserts it is REJECTED, not silently accepted."""

import ast
import inspect
import uuid

import pytest
from pydantic import ValidationError

from app.agents.matching import agent as matching_agent_module
from app.schemas.match import CriterionOutcome, EligibilityVerdict, MatchStrength, MatchVerdict


def test_agent_output_contradicting_hard_constraint_failure_is_rejected() -> None:
    """A Matching-agent output that claims 'eligible' despite a failed hard
    constraint (e.g. wrong nationality) MUST be rejected outright — the
    override attempt must fail, not merely be flagged."""
    failing_hard_constraint = CriterionOutcome(
        criterion="nationality", result="fail", evidence="Scholarship requires Pakistani or Indian nationality; student is Wronglandian."
    )

    with pytest.raises(ValidationError, match="hard-constraint"):
        MatchVerdict(
            scholarship_id=uuid.uuid4(),
            eligibility_verdict=EligibilityVerdict.ELIGIBLE,  # attempted override
            match_strength=MatchStrength.STRONG,
            hard_constraints=[failing_hard_constraint],
        )


def test_agent_output_contradicting_hard_constraint_failure_is_rejected_even_with_likely() -> None:
    """Same override-rejection guarantee for every non-'not' verdict value,
    not just 'eligible' — the guardrail must not have a narrow blind spot."""
    failing_hard_constraint = CriterionOutcome(criterion="deadline", result="fail", evidence="Deadline has passed.")

    for attempted_verdict in (EligibilityVerdict.LIKELY, EligibilityVerdict.POSSIBLY, EligibilityVerdict.UNKNOWN_REQUIRES_VERIFICATION):
        with pytest.raises(ValidationError, match="hard-constraint"):
            MatchVerdict(
                scholarship_id=uuid.uuid4(),
                eligibility_verdict=attempted_verdict,
                match_strength=MatchStrength.POSSIBLE,
                hard_constraints=[failing_hard_constraint],
            )


def test_matched_criterion_without_evidence_is_rejected() -> None:
    """A matched/failed criterion lacking an evidence reference MUST be
    rejected by the output guardrail — no-guess, evidence-required."""
    unsupported_pass = CriterionOutcome(criterion="gpa", result="pass", evidence=None)

    with pytest.raises(ValidationError, match="evidence"):
        MatchVerdict(
            scholarship_id=uuid.uuid4(),
            eligibility_verdict=EligibilityVerdict.LIKELY,
            match_strength=MatchStrength.POSSIBLE,
            hard_constraints=[unsupported_pass],
        )


def test_failed_soft_preference_without_evidence_is_rejected() -> None:
    unsupported_fail = CriterionOutcome(criterion="research_fit", result="unmet", evidence="")

    with pytest.raises(ValidationError, match="evidence"):
        MatchVerdict(
            scholarship_id=uuid.uuid4(),
            eligibility_verdict=EligibilityVerdict.LIKELY,
            match_strength=MatchStrength.POSSIBLE,
            hard_constraints=[],
            soft_preferences=[unsupported_fail],
        )


def test_unknown_result_never_requires_evidence_and_is_accepted() -> None:
    """The no-guess rule cuts the other way too: an honest 'unknown' must
    never be blocked just because it lacks evidence (there being no evidence
    is exactly why it's unknown)."""
    verdict = MatchVerdict(
        scholarship_id=uuid.uuid4(),
        eligibility_verdict=EligibilityVerdict.UNKNOWN_REQUIRES_VERIFICATION,
        match_strength=MatchStrength.POSSIBLE,
        hard_constraints=[CriterionOutcome(criterion="gpa", result="unknown")],
    )
    assert verdict.hard_constraints[0].evidence is None


def test_matching_agent_module_has_no_web_or_fetch_tool_access() -> None:
    """Bounded tool access (constitution Principle II): the Matching agent
    module must not import web_fetch, search, or any raw HTTP client —
    verified by static AST inspection of actual import statements (not a
    raw substring scan, which would also flag this guardrail's own
    docstring for merely naming the forbidden tools)."""
    source = inspect.getsource(matching_agent_module)
    tree = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
            imported_modules.update(f"{node.module}.{alias.name}" for alias in node.names)

    forbidden_module_fragments = ["httpx", "requests", "web_fetch", "tools.search"]
    for fragment in forbidden_module_fragments:
        offending = [m for m in imported_modules if fragment in m]
        assert not offending, f"Matching agent must not import {fragment!r} (no web/fetch tool access): found {offending}"
