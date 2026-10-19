"""Shared builders for the 002 US2 (re-match/alert) tests.

Everything runs on a rolled-back session. `build_monitor_env` mirrors the
fixture in tests/workflows/test_source_monitor.py (scoped sources, injected
fetch/extract seams, patched classifier) and additionally:
- scopes owner selection to the users a test created (the shared DB may hold
  real profiles that must never be re-matched or alerted from a test);
- stubs the soft-preference LLM call (the hard-constraint path is the thing
  under test; ADR-0007 accepts the soft-preference call).
"""

import uuid
from datetime import date, timedelta
from urllib.parse import urlparse

from app.data.repositories import alert_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringTrigger
from app.models.profile import Profile
from app.models.scholarship import Scholarship
from app.models.user import User
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor.graph import run_source_monitor

FUTURE = (date.today() + timedelta(days=90)).isoformat()
FUTURE_2 = (date.today() + timedelta(days=120)).isoformat()
PAST = "2020-01-01"

FAKE_CLASSIFICATION = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred",
    funding_status="fully_funded", funding_status_confidence="inferred",
    provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred",
    field="Computer Science", field_confidence="inferred",
)


class FakeChannel:
    """A NotificationChannel that records calls and sets its delivery column."""

    def __init__(self, name: str, *, ok: bool = True, error: str | None = None):
        self.name = name
        self.ok = ok
        self.error = error
        self.calls: list = []

    def send(self, alert, context) -> bool:
        from datetime import datetime, timezone

        self.calls.append((alert.id, context))
        if self.name == "email":
            if self.ok:
                alert.delivered_email_at, alert.email_error = datetime.now(timezone.utc), None
            else:
                alert.email_error = self.error or "boom"
        elif self.name == "in_app":
            alert.delivered_in_app_at = datetime.now(timezone.utc)
        return self.ok


def make_user(session, label: str = "u") -> User:
    user = User(email=f"{label}-{uuid.uuid4().hex}@example.com", password_hash="x")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def make_profile(session, user_id: uuid.UUID) -> Profile:
    profile = Profile(user_id=user_id)
    session.add(profile)
    session.commit()
    return profile


def make_scholarship(session, *, name: str | None = None, deadline: str | None = FUTURE) -> Scholarship:
    scholarship = Scholarship(
        name=name or f"Alert Test {uuid.uuid4().hex[:8]}",
        official_scholarship_url=f"https://alerts.example.test/{uuid.uuid4().hex}",
        deadline=date.fromisoformat(deadline) if deadline else None,
    )
    return scholarship_repo.create(session, scholarship)


def cand(name, **overrides) -> ExtractedListingCandidate:
    base = dict(name=name, degree_level="PhD", funding_status="fully_funded", deadline=FUTURE)
    return ExtractedListingCandidate(**{**base, **overrides})


class _Listing:
    def __init__(self):
        self.by_host: dict[str, list[ExtractedListingCandidate]] = {}

    def __call__(self, html, *, source_url):
        candidates = self.by_host.get(urlparse(source_url).hostname, [])
        return ExtractListingOutput(candidates=candidates, accepted_count=len(candidates), rejected_count=0)


def _fetcher(url):
    return FetchResult(url=url, success=True, status_code=200, html="<html>listing</html>")


def build_monitor_env(db_session_factory, monkeypatch):
    session = db_session_factory()
    scoped_ids: set[uuid.UUID] = set()
    real = source_repo.get_active_sources
    monkeypatch.setattr(
        source_repo, "get_active_sources", lambda db, *a, **k: [s for s in real(db, *a, **k) if s.id in scoped_ids]
    )
    monkeypatch.setattr(
        "app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: FAKE_CLASSIFICATION
    )
    monkeypatch.setattr("app.services.matching.produce_soft_preference_outcomes", lambda *a, **k: ([], []))

    class Env:
        pass

    e = Env()
    e.session, e.listing, e.users = session, _Listing(), []
    e.uid = uuid.uuid4().hex[:8]
    monkeypatch.setattr(alert_repo, "profile_user_ids", lambda db: list(e.users))

    def add_owner(label="owner"):
        user = make_user(session, label)
        make_profile(session, user.id)
        e.users.append(user.id)
        return user

    def make_source(label="s"):
        source = source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name=f"Alert Env {label}", source_type="gov", official_status="official",
                domain=f"alertenv-{label}-{e.uid}.example.com", access_method="web", discovery_role=True,
                reliability_level="high", status="active",
            ),
        )
        scoped_ids.add(source.id)
        return source

    def run(**kwargs):
        return run_source_monitor(
            session, trigger=MonitoringTrigger.MANUAL, fetch=_fetcher, extract_listing=e.listing,
            sleep=lambda _d: None, **kwargs,
        )

    e.add_owner, e.make_source, e.run = add_owner, make_source, run
    e.name = lambda label: f"{label} Scholarship {e.uid}"
    return e
