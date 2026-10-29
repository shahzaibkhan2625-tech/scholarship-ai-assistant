"""Contract tests: GET /sources/candidates/{id}/validations (002 US7 T239,
FR-CANDVAL-2/3/4, SC-006, US7 Scenarios 2-5). Automated results inform the human
gate; they never replace it. Runs in the rolled-back DB transaction."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import candidate_validation_repo, source_repo
from app.models.source import CandidateSource, CandidateSourceStatus
from app.schemas.source import CandidateSourceCreate

_T0 = datetime(2027, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


@pytest.fixture
def operator_user(client, make_user):
    """010: the gated routes need an operator; same shape as `authed_user` (no cleanup: rolled back)."""
    return {"client": client, "headers": make_user(is_operator=True)["headers"]}


def _candidate(session):
    return source_repo.create_candidate_source(
        session, CandidateSourceCreate(url=f"https://cand-{uuid.uuid4().hex[:8]}.example.org/scholarships")
    )


def _validate(session, candidate, minute, *, reachable=True, extractable=True, signals=None):
    return candidate_validation_repo.add(
        session,
        candidate.id,
        reachable=reachable,
        reachable_detail="HTTP 200" if reachable else "Could not reach URL",
        extractable=extractable,
        extractable_detail="3 grounded listings" if extractable else "0 grounded listings",
        official_signals=signals or {},
        checked_at=_T0 + timedelta(minutes=minute),
    )


def _approve_body(candidate):
    domain = candidate.url.split("//", 1)[1].split("/", 1)[0]
    return {
        "name": "Approved By Human", "source_type": "gov", "official_status": "official", "domain": domain,
        "access_method": "web", "discovery_role": True, "reliability_level": "high",
    }


def _release_reviewer(session, candidate_id):
    # operator_user teardown deletes the user; a reviewed_by FK would block that.
    session.query(CandidateSource).filter(CandidateSource.id == candidate_id).update({"reviewed_by": None})
    session.commit()


def _get(operator_user, candidate_id):
    return operator_user["client"].get(f"/sources/candidates/{candidate_id}/validations", headers=operator_user["headers"])


def test_requires_authentication(client):
    assert client.get(f"/sources/candidates/{uuid.uuid4()}/validations").status_code in (401, 403)


def test_unknown_candidate_is_404(operator_user):
    assert _get(operator_user, uuid.uuid4()).status_code == 404


def test_candidate_without_validations_returns_an_empty_list(operator_user, session):
    response = _get(operator_user, _candidate(session).id)
    assert response.status_code == 200
    assert response.json() == []


def test_history_is_newest_first_and_carries_every_contract_field(operator_user, session):
    candidate = _candidate(session)
    older = _validate(session, candidate, 0, reachable=False, extractable=False)
    newer = _validate(session, candidate, 5, signals={"domain_patterns": [".gov"], "affiliation_terms": ["ministry"]})
    body = _get(operator_user, candidate.id).json()
    assert [row["id"] for row in body] == [str(newer.id), str(older.id)]
    top = body[0]
    assert set(top) == {
        "id", "candidate_source_id", "checked_at", "reachable", "reachable_detail",
        "extractable", "extractable_detail", "official_signals",
    }
    assert top["candidate_source_id"] == str(candidate.id)
    assert top["official_signals"] == {"domain_patterns": [".gov"], "affiliation_terms": ["ministry"]}
    assert body[1]["reachable"] is False and body[1]["reachable_detail"] == "Could not reach URL"


def test_other_candidates_history_is_not_mixed_in(operator_user, session):
    mine, other = _candidate(session), _candidate(session)
    _validate(session, other, 1)
    assert _get(operator_user, mine.id).json() == []


def test_viewing_validations_does_not_change_the_candidate(operator_user, session):
    candidate = _candidate(session)
    _validate(session, candidate, 0)
    _get(operator_user, candidate.id)
    session.refresh(candidate)
    assert candidate.status == CandidateSourceStatus.PENDING


def test_all_pass_candidate_has_no_registry_row_until_a_human_approves(operator_user, session):
    candidate = _candidate(session)
    _validate(session, candidate, 0, signals={"domain_patterns": [".gov"]})
    domain = _approve_body(candidate)["domain"]
    assert source_repo.get_source_by_domain(session, domain) is None  # SC-006
    assert candidate.id not in {s.id for s in source_repo.get_active_sources(session)}

    approved = operator_user["client"].post(
        f"/sources/candidates/{candidate.id}/approve", json=_approve_body(candidate), headers=operator_user["headers"]
    )
    try:
        assert approved.status_code == 200
        assert source_repo.get_source_by_domain(session, domain) is not None
    finally:
        _release_reviewer(session, candidate.id)


def test_candidate_failing_a_check_can_still_be_approved(operator_user, session):
    candidate = _candidate(session)
    _validate(session, candidate, 0, reachable=False, extractable=False)
    response = operator_user["client"].post(
        f"/sources/candidates/{candidate.id}/approve", json=_approve_body(candidate), headers=operator_user["headers"]
    )
    try:
        assert response.status_code == 200
        assert response.json()["status"] == "active"
        session.refresh(candidate)
        assert candidate.status == CandidateSourceStatus.APPROVED
    finally:
        _release_reviewer(session, candidate.id)


def test_all_pass_candidate_can_still_be_rejected(operator_user, session):
    candidate = _candidate(session)
    _validate(session, candidate, 0, signals={"domain_patterns": [".edu"], "affiliation_terms": ["university"]})
    response = operator_user["client"].post(
        f"/sources/candidates/{candidate.id}/reject", json={"reason": "not relevant"}, headers=operator_user["headers"]
    )
    try:
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"
        assert source_repo.get_source_by_domain(session, _approve_body(candidate)["domain"]) is None
    finally:
        _release_reviewer(session, candidate.id)


def test_validations_stay_readable_after_the_human_decision(operator_user, session):
    candidate = _candidate(session)
    _validate(session, candidate, 0)
    operator_user["client"].post(
        f"/sources/candidates/{candidate.id}/reject", json={"reason": "no"}, headers=operator_user["headers"]
    )
    try:
        assert len(_get(operator_user, candidate.id).json()) == 1
    finally:
        _release_reviewer(session, candidate.id)


def test_no_write_route_exists_for_validations(operator_user, session):
    candidate = _candidate(session)
    url = f"/sources/candidates/{candidate.id}/validations"
    for method in ("post", "put", "patch", "delete"):
        response = getattr(operator_user["client"], method)(url, headers=operator_user["headers"])
        assert response.status_code == 405, method
