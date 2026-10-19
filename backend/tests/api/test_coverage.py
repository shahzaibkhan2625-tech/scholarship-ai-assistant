"""Contract tests: GET /coverage (002 US4 T206, FR-COVAN-1..4, SC-003). Real
(rolled-back) DB shared with seed sources, so every count is asserted as a DELTA
against a baseline snapshot taken before the test seeds its own rows."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import scholarship_repo, source_repo
from app.models.monitoring import MonitoringRun, MonitoringTrigger
from app.models.source import CandidateSourceStatus
from app.models.scholarship import LifecycleStatus, Scholarship, VerificationStatus
from app.schemas.source import CandidateSourceCreate, SourceFetchLogCreate, SourceRegistryCreate

_BASE = datetime(2027, 2, 1, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _source(session, label, **overrides):
    data = dict(
        name=f"Coverage Test {label}", source_type="gov", official_status="official",
        domain=f"cov-{label}-{uuid.uuid4().hex[:8]}.example.com", access_method="web",
        discovery_role=True, reliability_level="high", status="active", country="Atlantis",
    )
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**{**data, **overrides}))


def _run(session, minute):
    run = MonitoringRun(trigger=MonitoringTrigger.SCHEDULED, started_at=_BASE + timedelta(minutes=minute))
    session.add(run)
    session.commit()
    return run


def _log(session, source, run, status, minute):
    return source_repo.log_fetch(
        session,
        SourceFetchLogCreate(
            source_id=source.id, status=status, monitoring_run_id=run.id if run else None,
            started_at=_BASE + timedelta(minutes=minute),
        ),
    )


def _scholarship(session, *, verification, lifecycle):
    return scholarship_repo.create(
        session,
        Scholarship(
            name=f"Cov {uuid.uuid4().hex[:8]}",
            official_scholarship_url=f"https://cov.example.test/{uuid.uuid4().hex}",
            verification_status=verification,
            lifecycle_status=lifecycle,
        ),
    )


def _get(user):
    response = user["client"].get("/coverage", headers=user["headers"])
    assert response.status_code == 200
    return response.json()


def test_coverage_requires_a_bearer_token(client):
    assert client.get("/coverage").status_code == 401
    assert client.get("/coverage", headers={"Authorization": "Bearer not-a-token"}).status_code == 401


def test_response_has_the_contract_shape_and_never_claims_complete_coverage(authed_user):
    body = _get(authed_user)
    assert {
        "as_of", "sources_configured", "sources_active", "sources_checked", "sources_failed",
        "candidate_sources_pending", "verified_opportunities_count", "last_checked_at",
        "countries_covered", "gaps", "claims_complete_coverage",
    } <= set(body)
    assert body["claims_complete_coverage"] is False


def test_counts_match_the_seeded_registry_and_fetch_log_exactly(authed_user, session):
    before = _get(authed_user)

    ok = _source(session, "ok", country="Atlantis")
    failing_run = _source(session, "frun", country="Atlantis", source_type="university")
    failing_status = _source(session, "fstat", country="Lemuria", status="failing")
    disabled = _source(session, "dis", country="Mu", status="disabled")
    unchecked = _source(session, "unch", country="Mu", source_type="university")

    r1, r2 = _run(session, 0), _run(session, 10)
    _log(session, ok, r1, "ok", 1)
    _log(session, failing_run, r1, "ok", 1)  # an old good run...
    _log(session, failing_run, r2, "ok", 11)  # ...then a failed one: OK HTTP row + FAIL row
    _log(session, failing_run, r2, "fail", 12)
    _log(session, failing_status, r2, "ok", 11)
    _log(session, disabled, None, "fail", 5)

    after = _get(authed_user)

    def delta(key):
        return after[key] - before[key]

    assert delta("sources_configured") == 5
    assert delta("sources_active") == 4  # ok, failing_run, failing_status (active-but-unhealthy), unchecked
    assert delta("sources_checked") == 4  # ok, failing_run, failing_status, disabled
    assert delta("sources_failed") == 2  # failing_run (latest run failed) + disabled (raw row, no run)
    assert str(unchecked.id) not in after["last_checked_at"]
    assert after["last_checked_at"][str(failing_run.id)].startswith("2027-02-01T00:12:00")
    assert "Atlantis" in after["countries_covered"]  # `ok` is a healthy gov source there
    assert any("Atlantis" in g and "university" in g for g in after["gaps"])  # its only university source failed
    lemuria = [g for g in after["gaps"] if "Lemuria" in g]
    assert len(lemuria) == 1 and "no active source configured" not in lemuria[0]


def test_latest_run_wins_and_ingestion_rows_do_not_fail_a_healthy_source(authed_user, session):
    source = _source(session, "ing", country="Hy-Brasil")
    old, new = _run(session, 0), _run(session, 10)
    _log(session, source, old, "ok", 1)
    _log(session, source, old, "fail", 2)  # old failed run
    _log(session, source, new, "ok", 11)  # recovered
    _log(session, source, None, "fail", 20)  # later per-scholarship ingestion failure
    body = _get(authed_user)
    assert "Hy-Brasil" in body["countries_covered"]
    assert not any("Hy-Brasil" in g for g in body["gaps"])
    assert body["last_checked_at"][str(source.id)].startswith("2027-02-01T00:20:00")


def test_pending_candidates_are_counted_separately_from_sources(authed_user, session):
    before = _get(authed_user)
    source_repo.create_candidate_source(session, CandidateSourceCreate(url="https://cand-a.example.org/x"))
    rejected = source_repo.create_candidate_source(session, CandidateSourceCreate(url="https://cand-b.example.org/x"))
    # No `reviewed_by`: the authed_user teardown deletes the user, which a FK from the candidate would block.
    rejected.status = CandidateSourceStatus.REJECTED
    session.commit()
    after = _get(authed_user)
    assert after["candidate_sources_pending"] - before["candidate_sources_pending"] == 1
    assert after["sources_configured"] == before["sources_configured"]


def test_verified_opportunities_exclude_closed_and_expired(authed_user, session):
    before = _get(authed_user)["verified_opportunities_count"]
    _scholarship(session, verification=VerificationStatus.VERIFIED, lifecycle=LifecycleStatus.VERIFIED)
    _scholarship(session, verification=VerificationStatus.VERIFIED, lifecycle=LifecycleStatus.UPDATED)
    _scholarship(session, verification=VerificationStatus.VERIFIED, lifecycle=LifecycleStatus.CLOSED)
    _scholarship(session, verification=VerificationStatus.VERIFIED, lifecycle=LifecycleStatus.EXPIRED)
    _scholarship(session, verification=VerificationStatus.UNVERIFIED, lifecycle=LifecycleStatus.VERIFIED)
    assert _get(authed_user)["verified_opportunities_count"] - before == 2


def test_coverage_is_live_not_cached(authed_user, session):
    first = _get(authed_user)
    _source(session, "live", country="Thule")
    second = _get(authed_user)
    assert second["sources_configured"] == first["sources_configured"] + 1
