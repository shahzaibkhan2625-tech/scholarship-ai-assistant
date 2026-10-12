"""T212/T215: US5 end-to-end lifecycle walk (quickstart US5, FR-LIFECYCLE-1..5, SC-004).

One seeded scholarship is walked through discovered -> verified -> updated ->
stale -> source_unavailable -> closed -> reopened by real monitoring runs (fixture
fetch/extract seams, a controllable clock via `run_source_monitor(now=...)`),
asserting through `GET /scholarships/{id}` at every step that exactly one of the
nine lifecycle values is reported. Real (rolled-back) DB."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.data.repositories import scholarship_repo, source_repo
from app.models.monitoring import MonitoringTrigger
from app.models.scholarship import LifecycleStatus
from app.schemas.source import SourceRegistryCreate
from app.workflows.source_monitor.graph import run_source_monitor
from tests.workflows.test_source_monitor import _FAKE_CLASSIFICATION, _cand, _Fetcher, _Listing

NINE = {s.value for s in LifecycleStatus}


class _Walk:
    def __init__(self, session, authed_user, monkeypatch):
        self.session, self.user = session, authed_user
        self.uid = uuid.uuid4().hex[:8]
        self.t0 = datetime.now(timezone.utc)
        self.clock = self.t0
        self.listing, self.fetcher = _Listing(), _Fetcher()
        self.alpha, self.beta = f"Alpha Scholarship {self.uid}", f"Beta Scholarship {self.uid}"
        data = dict(
            name="Walk Source", source_type="gov", official_status="official", domain=f"walk-{self.uid}.example.com",
            access_method="web", discovery_role=True, reliability_level="high", status="active",
        )
        self.source = source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**data))
        real = source_repo.get_monitorable_sources
        monkeypatch.setattr(
            source_repo, "get_monitorable_sources",
            lambda db, *a, **k: [s for s in real(db, *a, **k) if s.id == self.source.id],
        )
        monkeypatch.setattr(
            "app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: _FAKE_CLASSIFICATION
        )

    def set_listing(self, *cands):
        self.listing.by_host[self.source.domain] = list(cands)

    def run(self, *, days: float):
        self.clock = self.t0 + timedelta(days=days)
        return run_source_monitor(
            self.session, trigger=MonitoringTrigger.MANUAL, fetch=self.fetcher, extract_listing=self.listing,
            sleep=lambda _d: None, now=lambda: self.clock,
        )

    def alpha_id(self):
        return {s.name: s for s in scholarship_repo.list_for_source(self.session, self.source.id)}[self.alpha].id

    def api_state(self):
        self.session.expire_all()
        response = self.user["client"].get(f"/scholarships/{self.alpha_id()}", headers=self.user["headers"])
        assert response.status_code == 200
        value = response.json()["lifecycle_status"]
        assert isinstance(value, str) and value in NINE  # exactly one value, from the fixed set
        return value


@pytest.fixture
def walk(db_session_factory, authed_user, monkeypatch):
    session = db_session_factory()
    try:
        yield _Walk(session, authed_user, monkeypatch)
    finally:
        session.close()


def test_u5_test_walk(walk):
    A, B = walk.alpha, walk.beta

    # discovered
    walk.set_listing(_cand(A, deadline="2031-01-01"), _cand(B, deadline="2031-01-01"))
    walk.run(days=0)
    assert walk.api_state() == "newly_discovered"

    # verified: a matched, grounded, open re-sighting from the approved source confirms it
    walk.run(days=1)
    assert walk.api_state() == "verified"

    # updated: the deadline moved on the official listing
    walk.set_listing(_cand(A, deadline="2032-01-01"), _cand(B, deadline="2031-01-01"))
    walk.run(days=2)
    assert walk.api_state() == "updated"

    # stale: freshness window (default 180d) elapses with no successful re-verification of Alpha
    walk.set_listing(_cand(B, deadline="2031-01-01"))  # Alpha not listed; listing is not asserted complete
    walk.run(days=2 + 200)
    assert walk.api_state() == "stale"

    # source_unavailable: retries exhausted; never expired/closed
    walk.fetcher.failing_hosts.add(walk.source.domain)
    walk.run(days=2 + 201)
    assert walk.api_state() == "source_unavailable"
    walk.fetcher.failing_hosts.clear()

    # closed: the source (now asserted a complete listing) stops listing it after a successful grounded fetch
    walk.source.extraction_rules = {"listing_complete": True}
    walk.session.commit()
    walk.run(days=2 + 202)
    assert walk.api_state() == "closed"

    # reopened: listed open again - never back to newly_discovered
    walk.set_listing(_cand(A, deadline="2032-01-01"), _cand(B, deadline="2031-01-01"))
    walk.run(days=2 + 203)
    state = walk.api_state()
    assert state == "reopened" and state != "newly_discovered"


def test_fetch_failure_never_closes_or_expires_at_any_step(walk):
    walk.set_listing(_cand(walk.alpha, deadline="2031-01-01"))
    walk.run(days=0)
    for day in (1, 2, 3):
        walk.fetcher.failing_hosts.add(walk.source.domain)
        walk.run(days=day)
        assert walk.api_state() not in ("closed", "expired")
    assert walk.api_state() == "source_unavailable"


def test_source_unavailable_recovers_to_verified_when_confirmed(walk):
    walk.set_listing(_cand(walk.alpha, deadline="2031-01-01"))
    walk.run(days=0)
    walk.run(days=1)
    assert walk.api_state() == "verified"
    walk.fetcher.failing_hosts.add(walk.source.domain)
    walk.run(days=2)
    assert walk.api_state() == "source_unavailable"
    walk.fetcher.failing_hosts.clear()
    walk.run(days=3)
    assert walk.api_state() == "verified"
