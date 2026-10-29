"""010 T363: approving a candidate cannot register a non-public domain (FR-SSRF-6) and a duplicate
registry domain is a clean 409, not a 500 (FR-SSRF-7). Runs in the rolled-back DB transaction;
the resolver is injected (no real DNS)."""

import socket
import uuid

import pytest

from app.data.repositories import source_repo
from app.models.source import CandidateSource, CandidateSourceStatus
from app.schemas.source import CandidateSourceCreate

DNS = {
    "sneaky.example.com": ["10.0.0.5"],
    "meta.example.com": ["169.254.169.254"],
    "mcast.example.com": ["224.0.0.1"],
    "good.example.com": ["93.184.216.34"],
}


def _resolve(host: str) -> list[str]:
    if host in DNS:
        return list(DNS[host])
    raise socket.gaierror(host)


@pytest.fixture(autouse=True)
def _resolver(monkeypatch):
    monkeypatch.setattr("app.api.sources._resolve_host", _resolve)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


@pytest.fixture
def operator_user(client, make_user):
    return {"client": client, "headers": make_user(is_operator=True)["headers"]}


def _candidate(session):
    return source_repo.create_candidate_source(
        session, CandidateSourceCreate(url=f"https://cand-{uuid.uuid4().hex[:8]}.example.org/scholarships")
    )


def _body(domain: str) -> dict:
    return {
        "name": "Approved By Human", "source_type": "gov", "official_status": "official", "domain": domain,
        "access_method": "web", "discovery_role": True, "reliability_level": "high",
    }


def _approve(operator_user, candidate, domain: str):
    return operator_user["client"].post(
        f"/sources/candidates/{candidate.id}/approve", json=_body(domain), headers=operator_user["headers"]
    )


def _release_reviewer(session, candidate_id):
    session.query(CandidateSource).filter(CandidateSource.id == candidate_id).update({"reviewed_by": None})
    session.commit()


@pytest.mark.parametrize(
    "domain,rule",
    [
        ("internal.corp", "'.corp'"),
        ("localhost", "single-label"),
        ("10.0.0.5", "IP address"),
        ("169.254.169.254", "IP address"),
        ("[::1]", None),  # not even a bare hostname for the schema
        ("intranet", "single-label"),
        ("printer.lan", "'.lan'"),
        ("app.localhost", "'.localhost'"),
        ("host.internal", "'.internal'"),
        ("sneaky.example.com", "10.0.0.5"),  # resolves to a private address
        ("meta.example.com", "169.254.169.254"),
        ("mcast.example.com", "224.0.0.1"),
    ],
)
def test_non_public_domain_is_422_naming_the_rule_and_nothing_is_created(operator_user, session, domain, rule):
    candidate = _candidate(session)

    response = _approve(operator_user, candidate, domain)

    assert response.status_code == 422
    if rule is not None:
        assert rule in response.text
    assert source_repo.get_source_by_domain(session, domain) is None
    session.refresh(candidate)
    assert candidate.status == CandidateSourceStatus.PENDING
    assert candidate.reviewed_by is None


def test_duplicate_registry_domain_is_409_and_candidate_stays_pending(operator_user, session):
    first, second = _candidate(session), _candidate(session)
    ok = _approve(operator_user, first, "good.example.com")
    try:
        assert ok.status_code == 200
        response = _approve(operator_user, second, "good.example.com")

        assert response.status_code == 409
        assert response.json()["detail"] == "Source domain already registered"
        session.refresh(second)
        assert second.status == CandidateSourceStatus.PENDING
        assert second.reviewed_by is None
    finally:
        _release_reviewer(session, first.id)


def test_public_domain_is_approved_as_today(operator_user, session):
    candidate = _candidate(session)
    response = _approve(operator_user, candidate, "good.example.com")
    try:
        assert response.status_code == 200
        assert response.json()["domain"] == "good.example.com"
        assert source_repo.get_source_by_domain(session, "good.example.com") is not None
        session.refresh(candidate)
        assert candidate.status == CandidateSourceStatus.APPROVED
    finally:
        _release_reviewer(session, candidate.id)


def test_domain_that_does_not_resolve_yet_is_still_approved(operator_user, session):
    candidate = _candidate(session)
    response = _approve(operator_user, candidate, "not-delegated-yet.example.com")
    try:
        assert response.status_code == 200
    finally:
        _release_reviewer(session, candidate.id)
