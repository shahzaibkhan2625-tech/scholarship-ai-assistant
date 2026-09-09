"""Main Orchestrator — intent routing across the Phase 1 capabilities
(profile, url-match, matching, Q&A) plus deterministic dispatch onto their
existing API-layer services (T030, T051, T052, T059).

Routing is deterministic and rule-based, not an LLM guess: FR-ROUTE-1
requires that when the system cannot confidently determine the user's
intent it MUST NOT guess or act on an uncertain interpretation, and
FR-ROUTE-2 requires it to ask for clarification instead. A message that
references a capability not yet built in this phase (discovery/"find",
generation/"draft SOP or CV" — Phase 2+) is always routed to clarification,
never silently dropped or misrouted onto a Phase 1 capability. FR-ROUTE-3:
when a message legitimately spans more than one *supported* capability, the
router returns an ordered sequence instead of forcing a single intent.
"""

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session

from app.agents.discovery.agent import run_discovery
from app.agents.research_qa.agent import answer_question
from app.data.repositories import match_repo, scholarship_repo
from app.models.user import User
from app.schemas.match import CriterionOutcome, EligibilityVerdict, MatchStrength, MatchVerdict
from app.schemas.profile import Profile as ProfileSchema
from app.services.matching import match_scholarship
from app.services.profile import get_or_create_profile
from app.services.ranking import rank_matches
from app.workflows.url_match.graph import run_url_match

_URL_RE = re.compile(r"https?://\S+")


class Intent(str, Enum):
    PROFILE = "profile"
    URL_MATCH = "url_match"
    MATCH_EXISTING = "match_existing"
    LIST_MATCHES = "list_matches"
    QA = "qa"
    DISCOVERY = "discovery"


# Capabilities that exist later on the roadmap but are not built yet.
# Mentioning one of these must never be silently ignored or misrouted onto
# another capability — it always forces a clarification request. (Discovery
# was in this list through Phase 1; Phase 2/T089-T091 built it, so it now
# routes via _DISCOVERY_KEYWORDS below instead of landing here.)
_UNSUPPORTED_CAPABILITY_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b(draft|write|generate)\b.{0,20}\b(sop|cv|statement of purpose)\b", re.I), "drafting a CV or SOP isn't available yet"),
)

_PROFILE_KEYWORDS = ("my profile", "update my", "my gpa", "my education", "my test score", "my experience", "profile criteria")
_LIST_MATCH_KEYWORDS = ("my matches", "list my matches", "matches so far", "what have i matched")
_DISCOVERY_KEYWORDS = ("find scholarships", "discover", "search for scholarships", "what scholarships match me")
_MATCH_KEYWORDS = ("match", "eligib", "am i eligible", "qualify")
_QA_KEYWORDS = ("?", "what is", "what's", "how much", "when is", "does it", "is there", "can i")


@dataclass(frozen=True)
class RoutedAction:
    intent: Intent
    url: str | None = None
    question: str | None = None
    scholarship_id: uuid.UUID | None = None


@dataclass(frozen=True)
class RoutingDecision:
    actions: tuple[RoutedAction, ...] = field(default_factory=tuple)
    needs_clarification: bool = False
    clarification_message: str | None = None

    @property
    def is_actionable(self) -> bool:
        return not self.needs_clarification and bool(self.actions)


_GENERIC_CLARIFICATION = (
    "I'm not sure what you'd like me to do. I can help with your profile, "
    "matching a scholarship from a link, checking your existing matches, or "
    "answering questions about a specific scholarship you're viewing. Could "
    "you clarify?"
)


def _clarify(message: str) -> RoutingDecision:
    return RoutingDecision(needs_clarification=True, clarification_message=message)


def route_message(
    text: str,
    *,
    active_scholarship_id: uuid.UUID | None = None,
) -> RoutingDecision:
    """Deterministic, keyword-based routing. `active_scholarship_id` is the
    scholarship currently in view/context (e.g. the page the user is on),
    used to resolve intents like "match this" or "what is the deadline?"
    that refer to a scholarship without naming it explicitly. Without that
    context, such requests cannot be resolved without guessing which
    scholarship is meant, so they fall back to clarification."""
    for pattern, reason in _UNSUPPORTED_CAPABILITY_PATTERNS:
        if pattern.search(text):
            return _clarify(
                f"I can't do that yet — {reason}. I can help with your profile, "
                "matching a scholarship you already have a link for, checking your "
                "existing matches, or answering questions about a specific "
                "scholarship. Could you clarify what you'd like me to do first?"
            )

    lowered = text.lower()
    urls = _URL_RE.findall(text)

    wants_profile = any(k in lowered for k in _PROFILE_KEYWORDS)
    wants_list_matches = any(k in lowered for k in _LIST_MATCH_KEYWORDS)
    wants_discovery = any(k in lowered for k in _DISCOVERY_KEYWORDS)
    # "what scholarships match me" contains "match" but is a discovery
    # request, not an eligibility check against a specific scholarship - it
    # must not also fan out to matching.
    wants_match = any(k in lowered for k in _MATCH_KEYWORDS) and not wants_list_matches and not wants_discovery
    # An eligibility-phrased question ("Am I eligible for this one?") is a
    # matching request, not a general content question - it must not also
    # fan out to Q&A just because it happens to end in "?".
    wants_qa = any(k in lowered for k in _QA_KEYWORDS) and not wants_match and not wants_discovery

    actions: list[RoutedAction] = []

    if wants_profile:
        actions.append(RoutedAction(intent=Intent.PROFILE))

    if wants_discovery:
        actions.append(RoutedAction(intent=Intent.DISCOVERY))

    if urls and (wants_match or not (wants_profile or wants_qa or wants_list_matches or wants_discovery)):
        # An explicit match request, or a bare link with no other signal —
        # the single unambiguous default for a bare link is to match it.
        for url in urls:
            actions.append(RoutedAction(intent=Intent.URL_MATCH, url=url))
    elif wants_list_matches:
        actions.append(RoutedAction(intent=Intent.LIST_MATCHES))
    elif wants_match:
        if active_scholarship_id is None:
            return _clarify(
                "Which scholarship would you like me to match against your profile? "
                "Please share its link, or open a specific scholarship first."
            )
        actions.append(RoutedAction(intent=Intent.MATCH_EXISTING, scholarship_id=active_scholarship_id))

    if wants_qa and not urls:
        if active_scholarship_id is None:
            # No scholarship in context: never dispatch a question to Q&A as
            # if it were about a specific, sourced scholarship (spec.md US3
            # Acceptance Scenario 4) — decline/redirect via clarification.
            return _clarify(
                "Which scholarship is your question about? Please open a specific "
                "scholarship, or share its link, so I can look up a grounded answer."
            )
        actions.append(RoutedAction(intent=Intent.QA, question=text, scholarship_id=active_scholarship_id))

    if not actions:
        return _clarify(_GENERIC_CLARIFICATION)

    return RoutingDecision(actions=tuple(actions))


@dataclass(frozen=True)
class ActionResult:
    intent: Intent
    payload: object


@dataclass(frozen=True)
class OrchestratorResponse:
    results: tuple[ActionResult, ...] = field(default_factory=tuple)
    needs_clarification: bool = False
    clarification_message: str | None = None


def _verdict_from_match_row(row) -> MatchVerdict:
    return MatchVerdict(
        scholarship_id=row.scholarship_id,
        eligibility_verdict=EligibilityVerdict(row.eligibility_verdict),
        match_strength=MatchStrength(row.match_strength),
        hard_constraints=[CriterionOutcome(**o) for o in row.hard_constraints],
        soft_preferences=[CriterionOutcome(**o) for o in row.soft_preferences],
        exclusions_triggered=row.exclusions_triggered,
        matched_criteria=row.matched_criteria,
        failed_criteria=row.failed_criteria,
        missing_information=row.missing_information,
        unverified_criteria=row.unverified_criteria,
        required_documents=row.required_documents,
        remaining_actions=row.remaining_actions,
        evidence=row.evidence,
    )


def handle_message(
    db: Session,
    user: User,
    text: str,
    *,
    active_scholarship_id: uuid.UUID | None = None,
) -> OrchestratorResponse:
    """Routes `text` and dispatches each resulting action onto the real
    Phase 1 capability (profile / url-match / matching / Q&A), in order.
    Returns a clarification instead of executing anything when the intent
    is uncertain, ambiguous, or references an unbuilt capability."""
    decision = route_message(text, active_scholarship_id=active_scholarship_id)
    if decision.needs_clarification:
        return OrchestratorResponse(needs_clarification=True, clarification_message=decision.clarification_message)

    results: list[ActionResult] = []
    for action in decision.actions:
        if action.intent is Intent.PROFILE:
            profile = get_or_create_profile(db, user.id)
            results.append(ActionResult(Intent.PROFILE, ProfileSchema.model_validate(profile)))

        elif action.intent is Intent.URL_MATCH:
            profile = get_or_create_profile(db, user.id)
            result = run_url_match(db, user.id, profile, action.url)
            results.append(ActionResult(Intent.URL_MATCH, result))

        elif action.intent is Intent.MATCH_EXISTING:
            scholarship = scholarship_repo.get_by_id(db, action.scholarship_id)
            if scholarship is None:
                results.append(ActionResult(Intent.MATCH_EXISTING, None))
                continue
            profile = get_or_create_profile(db, user.id)
            verdict = match_scholarship(db, user.id, profile, scholarship)
            results.append(ActionResult(Intent.MATCH_EXISTING, verdict))

        elif action.intent is Intent.LIST_MATCHES:
            rows = match_repo.list_for_user(db, user.id)
            verdicts = rank_matches([_verdict_from_match_row(row) for row in rows])
            results.append(ActionResult(Intent.LIST_MATCHES, verdicts))

        elif action.intent is Intent.QA:
            answer = answer_question(action.question, str(action.scholarship_id))
            results.append(ActionResult(Intent.QA, answer))

        elif action.intent is Intent.DISCOVERY:
            profile = get_or_create_profile(db, user.id)
            run = run_discovery(db, profile)
            results.append(ActionResult(Intent.DISCOVERY, run.discovery_result))

    return OrchestratorResponse(results=tuple(results))
