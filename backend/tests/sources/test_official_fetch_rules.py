"""003 T311 (FR-RULES-1..4, R-11): the rules-first path of the listing connector.

Rules run before any LLM request; a match is recorded as `outcome='rules'` with zero provider requests and an
`avoided_rules` ledger event; no match makes exactly one (gated) LLM request and flags the rules for review; an
anomalous count holds the result; rule results pass the same grounding gate. Real (rolled-back) DB; fake fetch,
fake clock, counting fake extractor. The HEC fixture is the committed trimmed copy of the real page."""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.llm_gate import LlmBudgetDeferred
from app.data.repositories import check_state_repo, llm_budget_repo, source_repo
from app.models.source import FetchStatus, SourceFetchLog
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.sources.connectors import official_fetch
from app.sources.connectors.official_fetch import fetch_and_extract_listing
from app.tools import rule_extract
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult

T0 = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
HEC_PAGE = (Path(__file__).resolve().parents[1] / "fixtures" / "hec" / "listing.html").read_text(encoding="utf-8")
RULES = {
    "version": 1,
    "enabled": True,
    "item_selector": "ul.card-info-container li.card",
    "name": {"attribute": "data-position"},
    "detail_url": {"selector": "a.read-more-arrow", "attribute": "href"},
    "provider_from_registry": True,
    "min_items": 20,
}
LLM_PAGE = "<html><body><ul><li>Alpha Scholarship</li><li>Beta Grant</li></ul></body></html>"


class Env:
    def __init__(self, db, source):
        self.db, self.source, self.now = db, source, T0
        self.html = HEC_PAGE
        self.extract_calls = 0
        self.error: Exception | None = None

    def clock(self):
        return self.now

    def fetch(self, url, headers=None):
        return FetchResult(url=url, success=True, status_code=200, html=self.html, final_url=url)

    def extract(self, html, *, source_url):
        self.extract_calls += 1
        if self.error is not None:
            raise self.error
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Alpha Scholarship")], accepted_count=1, rejected_count=0
        )

    def check(self):
        return fetch_and_extract_listing(
            self.db, self.source.id, None, fetch=self.fetch, extract_listing=self.extract, sleep=lambda s: None,
            clock=self.clock, bypass_cache=True, skip_failing_transition=True, allow_failing=True,
        )

    def state(self):
        self.db.expire_all()
        return check_state_repo.get(self.db, self.source.id)

    def rows(self):
        self.db.expire_all()
        stmt = select(SourceFetchLog).where(SourceFetchLog.source_id == self.source.id).order_by(
            SourceFetchLog.started_at, SourceFetchLog.id
        )
        return list(self.db.execute(stmt).scalars())

    def avoided_rules(self):
        day = T0.astimezone(ZoneInfo(settings.llm_budget_timezone)).date()
        return llm_budget_repo.count(self.db, day, "generate", "monitoring", "avoided_rules")


@pytest.fixture
def make_env(db_session_factory):
    sessions = []

    def _make(rules=RULES, organization="Higher Education Commission"):
        session = db_session_factory()
        sessions.append(session)
        source = source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name="Rules Test", organization=organization, source_type="gov", official_status="official",
                domain=f"rules-{uuid.uuid4().hex[:10]}.example.com", access_method="web", discovery_role=True,
                reliability_level="high", status="active", update_frequency="weekly",
                extraction_rules={"deterministic": rules} if rules is not None else {},
            ),
        )
        session.commit()
        return Env(session, source)

    yield _make
    for session in reversed(sessions):
        session.close()


def _priority():
    from app.core.llm_gate import Priority, llm_priority

    return llm_priority(Priority.MONITORING)


def test_a_rules_match_makes_no_provider_request_and_is_recorded_as_rules(make_env):
    env = make_env()
    with _priority():
        result = env.check()
    assert env.extract_calls == 0
    assert result.status == "ok" and result.accepted_count == 37
    assert len(result.candidates) == 37
    ok_rows = [r for r in env.rows() if r.items_found is not None]
    assert [(r.status, r.outcome, r.items_found) for r in ok_rows] == [(FetchStatus.OK, "rules", 37)]
    assert env.avoided_rules() == 1
    state = env.state()
    assert state.last_extraction_outcome == "rules" and state.fingerprint is not None
    assert state.rules_review_needed is False


def test_a_rules_extracted_page_is_then_skipped_when_unchanged(make_env):
    env = make_env()
    with _priority():
        env.check()
        env.now = T0 + timedelta(hours=1)
        again = env.check()
    assert again.status == "unchanged" and env.extract_calls == 0


def test_no_match_falls_back_to_exactly_one_gated_llm_request_and_flags_the_rules(make_env):
    env = make_env()
    env.html = LLM_PAGE  # the markup changed: the card selector matches nothing
    with _priority():
        result = env.check()
    assert env.extract_calls == 1
    assert result.status == "ok" and [c["name"] for c in result.candidates] == ["Alpha Scholarship"]
    state = env.state()
    assert state.rules_review_needed is True and state.rules_review_reason == "no_match"
    assert state.last_extraction_outcome == "ok"
    assert [r.outcome for r in env.rows() if r.items_found is not None] == [None]  # an LLM extraction row, as today


def test_the_fallback_still_obeys_the_budget(make_env):
    env = make_env()
    env.html = LLM_PAGE
    env.error = LlmBudgetDeferred(T0 + timedelta(hours=1))
    with _priority():
        result = env.check()
    assert result.status == "deferred" and env.extract_calls == 1
    assert env.state().rules_review_needed is True  # the rules did not match; that is still reported


def test_a_sharply_different_count_is_held_and_flagged_not_applied(make_env):
    env = make_env()
    source_repo.log_fetch(
        env.db,
        SourceFetchLogCreate(
            source_id=env.source.id, status=FetchStatus.OK, items_found=100,
            fetched_url=f"https://{env.source.domain}", started_at=T0 - timedelta(days=1),
        ),
    )
    with _priority():
        result = env.check()
    assert result.status == "deferred" and result.candidates == []
    assert env.extract_calls == 0  # held for the operator, no LLM request either
    state = env.state()
    assert state.rules_review_needed is True and state.rules_review_reason == "below_low_ratio"
    assert state.fingerprint is None  # the held result was not applied, so the page is still "new"
    assert env.avoided_rules() == 0
    assert state.next_attempt_at is not None and state.next_attempt_at > T0  # not re-evaluated on every tick


def test_rule_results_pass_the_same_grounding_gate(make_env):
    rules = {**RULES, "min_items": 1}
    env = make_env(rules=rules)
    env.html = (
        "<ul class='card-info-container'>"
        "<li class='card' data-position='Real Award'>Real Award</li>"
        "<li class='card' data-position='Phantom Prize'>unrelated words only</li></ul>"
    )
    with _priority():
        result = env.check()
    assert [c["name"] for c in result.candidates] == ["Real Award"]
    note = [r.error for r in env.rows() if r.items_found is not None][0]
    assert note and "Phantom Prize" in note and "ungrounded" in note


def test_provider_comes_from_the_registry_organization_and_nothing_else_is_guessed(make_env):
    env = make_env()
    with _priority():
        candidates = env.check().candidates
    assert {c["provider"] for c in candidates} == {"Higher Education Commission"}
    for candidate in candidates:
        assert not ({"country", "field", "degree_level", "deadline"} & set(candidate))
        assert candidate["funding_status"] == "unknown"
    no_org = make_env(organization=None)
    with _priority():
        assert all("provider" not in c for c in no_org.check().candidates)


@pytest.mark.parametrize("rules", [None, {**RULES, "enabled": False}])
def test_a_source_without_enabled_rules_never_touches_the_engine(make_env, monkeypatch, rules):
    def boom(*a, **k):
        raise AssertionError("the rule engine must not be invoked")

    monkeypatch.setattr(rule_extract, "rule_extract", boom)
    monkeypatch.setattr(official_fetch.rule_extract, "rule_extract", boom)
    env = make_env(rules=rules)
    env.html = LLM_PAGE
    with _priority():
        result = env.check()
    assert result.status == "ok" and env.extract_calls == 1
    assert env.state().rules_review_needed is False
