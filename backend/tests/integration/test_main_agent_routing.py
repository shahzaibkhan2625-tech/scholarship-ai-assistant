"""Integration test for the Main Orchestrator (backend/app/orchestration/main_agent.py).

Covers spec.md's routing Edge Case ("find and match scholarships for me,
then draft my SOP" must not guess at an ambiguous/multi-capability intent -
spec.md line ~124) and FR-ROUTE-1..3: routing must never guess on an
uncertain interpretation (FR-ROUTE-1), must ask for clarification instead
(FR-ROUTE-2), and may sequence multiple *supported* capabilities in one
message rather than forcing one intent per message (FR-ROUTE-3).

Pure routing behavior (`route_message`) needs no DB and always runs.
Dispatch behavior (`handle_message`) needs a real user + DB session (this
project has no local test DB - see tests/conftest.py - so it skips itself
when DATABASE_URL is absent/unreachable, exactly like the other Phase 1
integration tests)."""

import uuid
from unittest.mock import patch

from app.orchestration.main_agent import Intent, handle_message, route_message
from app.rag.retrieve import RetrievedChunk


# --- Pure routing: no DB required -------------------------------------------------


def test_edge_case_multi_capability_message_with_unbuilt_capability_requires_clarification() -> None:
    """spec.md Edge Case: "find and match scholarships for me, then draft my
    SOP" spans discovery (find) and generation (draft SOP) - neither built in
    Phase 1 - plus matching (which is). The router must not guess which part
    to honor; it must ask for clarification instead of silently dropping the
    unsupported parts or misrouting the whole message."""
    decision = route_message("find and match scholarships for me, then draft my SOP")

    assert decision.needs_clarification is True
    assert not decision.actions
    assert "discovery" in decision.clarification_message.lower() or "find" in decision.clarification_message.lower()


def test_draft_sop_alone_requires_clarification_not_misrouted() -> None:
    decision = route_message("Can you draft my SOP for this scholarship?")

    assert decision.needs_clarification is True
    assert "sop" in decision.clarification_message.lower()


def test_genuinely_ambiguous_message_requires_clarification() -> None:
    decision = route_message("hey, can you help me out with something")

    assert decision.needs_clarification is True
    assert not decision.actions


def test_bare_url_routes_unambiguously_to_url_match() -> None:
    decision = route_message("https://example.test/scholarships/some-award")

    assert decision.needs_clarification is False
    assert len(decision.actions) == 1
    assert decision.actions[0].intent is Intent.URL_MATCH
    assert decision.actions[0].url == "https://example.test/scholarships/some-award"


def test_question_without_scholarship_context_declines_rather_than_dispatching_to_qa() -> None:
    """spec.md US3 Acceptance Scenario 4 / quickstart.md: a question with no
    scholarship in context must not be dispatched to Q&A as if it were a
    grounded fact about a specific scholarship - the Main Agent's routing
    itself must decline/redirect."""
    decision = route_message("What career should I pursue in general?")

    assert decision.needs_clarification is True
    assert not decision.actions


def test_question_with_active_scholarship_context_routes_to_qa() -> None:
    scholarship_id = uuid.uuid4()
    decision = route_message("What is the stipend?", active_scholarship_id=scholarship_id)

    assert decision.needs_clarification is False
    assert len(decision.actions) == 1
    action = decision.actions[0]
    assert action.intent is Intent.QA
    assert action.scholarship_id == scholarship_id
    assert action.question == "What is the stipend?"


def test_match_request_without_scholarship_context_requires_clarification() -> None:
    decision = route_message("Am I eligible?")

    assert decision.needs_clarification is True


def test_match_request_with_active_scholarship_context_routes_to_match_existing() -> None:
    scholarship_id = uuid.uuid4()
    decision = route_message("Am I eligible for this one?", active_scholarship_id=scholarship_id)

    assert decision.needs_clarification is False
    assert len(decision.actions) == 1
    assert decision.actions[0].intent is Intent.MATCH_EXISTING
    assert decision.actions[0].scholarship_id == scholarship_id


def test_multi_capability_supported_sequence_is_executed_in_order() -> None:
    """FR-ROUTE-3: a message spanning more than one *supported* capability
    (profile + matches list, both Phase 1) is sequenced, not forced into a
    single intent or a clarification request."""
    decision = route_message("Check my profile and then list my matches so far")

    assert decision.needs_clarification is False
    intents = [a.intent for a in decision.actions]
    assert intents == [Intent.PROFILE, Intent.LIST_MATCHES]


def test_list_matches_phrase_alone_routes_to_list_matches() -> None:
    decision = route_message("Show me my matches so far")

    assert decision.needs_clarification is False
    assert len(decision.actions) == 1
    assert decision.actions[0].intent is Intent.LIST_MATCHES


# --- Dispatch: requires a real user + DB session ----------------------------------


def _user_from(db_session_factory, user_id: uuid.UUID):
    from app.models.user import User

    with db_session_factory() as session:
        return session.get(User, user_id)


def test_dispatch_sequences_profile_then_list_matches(authed_user, db_session_factory) -> None:
    client, headers, user_id = authed_user["client"], authed_user["headers"], authed_user["user_id"]
    client.put("/profile", headers=headers, json={"nationality": "Pakistani"})

    with db_session_factory() as session:
        from app.models.user import User

        user = session.get(User, user_id)

        response = handle_message(session, user, "Check my profile and then list my matches so far")

    assert response.needs_clarification is False
    assert len(response.results) == 2
    assert response.results[0].intent is Intent.PROFILE
    assert response.results[0].payload.nationality == "Pakistani"
    assert response.results[1].intent is Intent.LIST_MATCHES
    assert response.results[1].payload == []  # no matches created yet


def test_dispatch_routes_question_to_grounded_qa(authed_user, db_session_factory) -> None:
    client, headers, user_id = authed_user["client"], authed_user["headers"], authed_user["user_id"]
    scholarship_id = uuid.uuid4()
    chunk = RetrievedChunk(
        text="The monthly stipend for this scholarship is EUR 1200.",
        source_url="https://example.test/official-page",
        score=0.9,
        retrieved_at="2026-01-01T00:00:00+00:00",
    )

    with db_session_factory() as session:
        from app.models.user import User

        user = session.get(User, user_id)

        with (
            patch("app.agents.research_qa.agent.retrieve", return_value=[chunk]),
            patch("app.core.llm.generate_text", return_value="The monthly stipend is EUR 1200."),
        ):
            response = handle_message(
                session, user, "What is the stipend?", active_scholarship_id=scholarship_id
            )

    assert response.needs_clarification is False
    assert len(response.results) == 1
    assert response.results[0].intent is Intent.QA
    assert response.results[0].payload.confidence == "verified"


def test_dispatch_declines_unbuilt_capability_without_touching_db(authed_user, db_session_factory) -> None:
    """Even with a real user/session available, a message referencing an
    unbuilt capability must still be declined via clarification, not
    partially executed."""
    _, _, user_id = authed_user["client"], authed_user["headers"], authed_user["user_id"]

    with db_session_factory() as session:
        from app.models.user import User

        user = session.get(User, user_id)
        response = handle_message(session, user, "find and match scholarships for me, then draft my SOP")

    assert response.needs_clarification is True
    assert not response.results
