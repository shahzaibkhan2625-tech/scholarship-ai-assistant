"""003 T296 (FR-CADENCE-5/6, FR-OBS-3, plan decision 5): `run_source_monitor` checks only due sources, stalest first,
counts skipped and deferred, persists the counters and keeps the per-source lock. Real (rolled-back) DB; fake
fetch, counting fake extractor, fake clock through the `now` seam.

Also closes the known limit of T290: "a changed page is re-attempted every tick after a provider failure" is
stopped by `next_attempt_at` gating at due selection (the source is simply not due until the retry time)."""

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.core.llm_gate import LlmBudgetDeferred, LlmQuotaExhausted
from app.data.repositories import check_state_repo, source_repo
from app.models.monitoring import MonitoringTrigger
from app.schemas.source import SourceRegistryCreate
from app.services.classify import ClassifiedScholarshipFields
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor import graph as monitor_graph
from app.workflows.source_monitor.graph import run_source_monitor

CLASSIFIED = ClassifiedScholarshipFields(
    degree_level="PhD", degree_level_confidence="inferred", funding_status="fully_funded",
    funding_status_confidence="inferred", provider_type="gov", provider_type_confidence="inferred",
    country="Germany", country_confidence="inferred", field="Computer Science", field_confidence="inferred",
)


def _page(domain: str, version: int = 1) -> str:
    return f"<html><body><ul><li>{domain} Scholarship v{version}</li></ul></body></html>"


@pytest.fixture
def env(db_session_factory, monkeypatch):
    session = db_session_factory()
    base = datetime.now(timezone.utc).replace(microsecond=0)
    sources: dict[str, object] = {}
    state = {"fetched": [], "extract_error": {}, "version": {}, "extracted": []}

    def add(label: str, frequency: str = "weekly", **rules):
        domain = f"cad-{label}-{uuid.uuid4().hex[:8]}.example.com"
        source = source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name=f"Cadence {label}", source_type="gov", official_status="official", domain=domain,
                access_method="web", discovery_role=True, reliability_level="high", status="active",
                update_frequency=frequency, extraction_rules=rules,
            ),
        )
        session.commit()
        sources[label] = source
        return source

    def fetch(url, headers=None):
        domain = url.split("//")[1].split("/")[0]
        state["fetched"].append(domain)
        version = state["version"].get(domain, 1)
        return FetchResult(url=url, success=True, status_code=200, html=_page(domain, version), final_url=url)

    def extract(html, *, source_url):
        domain = source_url.split("//")[1].split("/")[0]
        state["extracted"].append(domain)
        error = state["extract_error"].get(domain)
        if error is not None:
            raise error
        candidates = [ExtractedListingCandidate(name=f"{domain} Scholarship", degree_level="PhD",
                                                funding_status="fully_funded", deadline="2027-03-01")]
        return ExtractListingOutput(candidates=candidates, accepted_count=1, rejected_count=0)

    monkeypatch.setattr(
        source_repo, "get_monitorable_sources",
        lambda db, **kw: [db.get(type(s), s.id) for s in sources.values()],
    )
    monkeypatch.setattr("app.workflows.ingestion.graph.classify_service.classify_candidate", lambda text: CLASSIFIED)

    class Env:
        pass

    e = Env()
    e.session, e.base, e.sources, e.state, e.add = session, base, sources, state, add

    def run(at=None, trigger=MonitoringTrigger.SCHEDULED):
        state["fetched"].clear()
        return run_source_monitor(
            session, trigger=trigger, fetch=fetch, extract_listing=extract, sleep=lambda _d: None,
            now=lambda: at or base,
        )

    def age(label: str, delta: timedelta):
        """Make a source look as if it was last checked `delta` before `base` (what a past tick would have done)."""
        session.expire_all()
        source = session.get(type(sources[label]), sources[label].id)
        source.last_checked_at = base - delta
        session.commit()

    e.run, e.age = run, age
    yield e
    session.close()


def _domain(env, label):
    return env.sources[label].domain


def test_only_due_sources_are_checked(env):
    env.add("due")
    env.add("quiet")
    env.age("due", timedelta(days=3))
    env.age("quiet", timedelta(hours=2))  # weekly -> interval 1 day: not due
    run = env.run()
    assert env.state["fetched"] == [_domain(env, "due")]
    assert run.sources_processed == 1


def test_a_never_checked_source_is_due(env):
    env.add("fresh")
    env.run()
    assert env.state["fetched"] == [_domain(env, "fresh")]


def test_due_sources_are_checked_stalest_first(env):
    for label, days in (("a", 3), ("b", 9), ("c", 5)):
        env.add(label)
        env.age(label, timedelta(days=days))
    env.run()
    assert env.state["fetched"] == [_domain(env, "b"), _domain(env, "c"), _domain(env, "a")]


def test_an_annual_source_is_not_read_every_tick(env):
    env.add("annual", frequency="annual")
    env.age("annual", timedelta(days=2))  # annual -> 7 days
    run = env.run()
    assert env.state["fetched"] == [] and run.sources_processed == 0


def test_the_interval_override_in_the_rules_takes_precedence(env):
    env.add("fast", frequency="annual", check_interval_minutes=30)
    env.age("fast", timedelta(hours=2))
    env.run()
    assert env.state["fetched"] == [_domain(env, "fast")]


def test_a_budget_deferred_source_goes_first_next_tick(env):
    env.add("a")
    env.add("b")
    env.state["extract_error"][_domain(env, "a")] = LlmBudgetDeferred(env.base + timedelta(minutes=10))
    first = env.run()
    assert first.sources_deferred == 1  # a: deferred, b: checked and touched
    env.state["extract_error"].clear()
    second = env.run(at=env.base + timedelta(hours=1))  # a's retry time has passed; b is not due for a day
    assert env.state["fetched"] == [_domain(env, "a")]
    assert second.sources_processed == 1


def test_an_idle_manual_run_still_creates_its_row(env):
    env.add("quiet")
    env.age("quiet", timedelta(hours=1))
    run = env.run(trigger=MonitoringTrigger.MANUAL)
    assert run.id is not None and run.status.value == "completed"
    assert (run.sources_processed, run.sources_skipped, run.sources_deferred) == (0, 0, 0)


def test_skipped_and_deferred_counters_are_persisted_and_logged(env, caplog):
    env.add("skip")
    env.add("defer")
    env.run()  # both extracted once; fingerprints stored
    for label in ("skip", "defer"):
        env.age(label, timedelta(days=3))
    env.state["version"][_domain(env, "defer")] = 2  # changed page
    env.state["extract_error"][_domain(env, "defer")] = LlmBudgetDeferred(env.base + timedelta(hours=1))
    with caplog.at_level(logging.INFO):
        run = env.run(at=env.base + timedelta(minutes=1))
    env.session.expire_all()
    stored = env.session.get(type(run), run.id)
    assert (stored.sources_processed, stored.sources_skipped, stored.sources_deferred) == (2, 1, 1)
    finished = [r for r in caplog.records if r.getMessage() == "monitoring run finished"][-1]
    assert (finished.sources_skipped, finished.sources_deferred) == (1, 1)


def test_the_per_source_lock_is_still_taken_for_every_checked_source(env, monkeypatch):
    env.add("a")
    env.add("b")
    taken = []

    @contextmanager
    def fake_lock(db, source_id):
        taken.append(source_id)
        yield True

    monkeypatch.setattr(monitor_graph, "source_lock", fake_lock)
    env.run()
    assert set(taken) == {env.sources["a"].id, env.sources["b"].id}


# -- the known limit of T290: a changed page after a provider failure ------------------------------------------------


def test_a_changed_page_is_not_retried_every_tick_after_a_provider_failure(env):
    env.add("hec")
    domain = _domain(env, "hec")
    env.run()  # baseline extraction at `base`
    env.age("hec", timedelta(days=3))
    env.state["version"][domain] = 2  # the page changed
    retry_at = env.base + timedelta(hours=2)
    env.state["extract_error"][domain] = LlmQuotaExhausted(retry_at=retry_at)

    env.run(at=env.base)  # tick 1: provider quota failure, retry time recorded
    assert env.state["fetched"] == [domain]
    session = env.session
    session.expire_all()
    assert check_state_repo.get(session, env.sources["hec"].id).next_attempt_at == retry_at

    for minutes in (10, 20, 59, 90):  # ticks 2..5, all before the retry time
        env.run(at=env.base + timedelta(minutes=minutes))
        assert env.state["fetched"] == [], f"re-attempted at +{minutes} min"

    env.state["extract_error"].clear()
    env.run(at=retry_at + timedelta(minutes=1))  # first tick after the retry time: one attempt, and it works
    assert env.state["fetched"] == [domain]
    assert env.state["extracted"][-1] == domain
