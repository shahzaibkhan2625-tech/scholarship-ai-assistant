"""003 T278 (FR-SKIP-2/4/5/6, R-07, R-08): the connector skips the LLM extraction for an unchanged page.
Real (rolled-back) DB; fake fetch, fake clock, counting fake extractor (no provider)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.llm_gate import LlmBudgetDeferred, LlmError
from app.data.repositories import check_state_repo, source_repo
from app.models.source import FetchStatus, SourceFetchLog
from app.schemas.source import SourceFetchLogCreate, SourceRegistryCreate
from app.sources.connectors.official_fetch import fetch_and_extract_listing
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
PAGE_A = "<html><body><ul><li>Alpha Scholarship</li><li>Beta Grant</li></ul></body></html>"
PAGE_B = "<html><body><ul><li>Alpha Scholarship</li><li>Beta Grant</li><li>Gamma Award</li></ul></body></html>"


class Env:
    def __init__(self, db, source):
        self.db, self.source = db, source
        self.now = T0
        self.html = PAGE_A
        self.headers_seen: list = []
        self.fetch_calls = 0
        self.extract_calls = 0
        self.zero = False
        self.response_headers: dict = {}
        self.extract_error: Exception | None = None

    def clock(self):
        return self.now

    def fetch(self, url, headers=None):
        self.fetch_calls += 1
        self.headers_seen.append(headers)
        if headers and self.response_headers.get("304"):
            return FetchResult(url=url, success=True, status_code=304, final_url=url, not_modified=True)
        return FetchResult(
            url=url, success=True, status_code=200, html=self.html, final_url=url,
            etag=self.response_headers.get("etag"), last_modified=self.response_headers.get("last_modified"),
        )

    def extract(self, html, *, source_url):
        self.extract_calls += 1
        if self.extract_error is not None:
            raise self.extract_error
        if self.zero:
            return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Alpha Scholarship")], accepted_count=1, rejected_count=0
        )

    def check(self, **kwargs):
        return fetch_and_extract_listing(
            self.db, self.source.id, None, fetch=self.fetch, extract_listing=self.extract,
            bypass_cache=True, skip_failing_transition=True, allow_failing=True, sleep=lambda s: None,
            clock=self.clock, **kwargs,
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


@pytest.fixture
def make_env(db_session_factory):
    sessions = []

    def _make(**source_fields):
        session = db_session_factory()
        sessions.append(session)
        fields = dict(
            name="FP Test", source_type="gov", official_status="official",
            domain=f"fp-{uuid.uuid4().hex[:10]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active", update_frequency="weekly",
        )
        fields.update(source_fields)
        source = source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**fields))
        session.commit()
        return Env(session, source)

    yield _make
    for session in sessions:
        session.close()


def test_first_ever_check_extracts_and_stores_the_fingerprint(make_env):
    env = make_env()
    result = env.check()
    assert result.status == "ok" and env.extract_calls == 1
    state = env.state()
    assert state.fingerprint and len(state.fingerprint) == 64
    assert state.last_extraction_outcome == "ok" and state.last_full_extraction_at == T0


def test_a_second_identical_check_does_not_call_the_extractor(make_env, monkeypatch):
    env = make_env()
    env.check()
    detections = []
    monkeypatch.setattr(
        "app.sources.connectors.official_fetch._detect_and_record_candidate_sources",
        lambda *a, **k: detections.append(1),
    )
    env.now = T0 + timedelta(hours=1)
    result = env.check()
    assert result.status == "unchanged" and env.extract_calls == 1
    assert detections == []  # same page, same links: candidate-link detection is skipped too
    unchanged = [r for r in env.rows() if r.outcome == "unchanged"]
    assert len(unchanged) == 1
    assert unchanged[0].status == FetchStatus.OK and unchanged[0].items_found is None


def test_unchanged_result_carries_the_persisted_refs_and_no_candidates(make_env):
    env = make_env()
    env.check()
    env.now = T0 + timedelta(hours=1)
    result = env.check()
    assert result.candidates == [] and result.accepted_count is None


def test_a_changed_page_extracts_once_and_stores_the_new_fingerprint(make_env):
    env = make_env()
    env.check()
    old = env.state().fingerprint
    env.html = PAGE_B
    env.now = T0 + timedelta(hours=1)
    result = env.check()
    assert result.status == "ok" and env.extract_calls == 2
    assert env.state().fingerprint != old


def test_a_deferred_extraction_leaves_the_fingerprint_untouched(make_env):
    env = make_env()
    env.check()
    old = env.state().fingerprint
    env.html = PAGE_B
    env.extract_error = LlmBudgetDeferred(T0 + timedelta(days=1))
    env.now = T0 + timedelta(hours=1)
    try:
        env.check()
    except LlmError:
        pass  # propagated today; recorded as a deferred result by T290 (US4)
    assert env.state().fingerprint == old


def test_an_unchanged_page_whose_last_extraction_failed_is_retried_with_doubling_backoff(make_env):
    env = make_env(update_frequency="weekly")  # cadence cap 1440 minutes
    env.zero = True
    env.check()
    assert env.extract_calls == 1
    state = env.state()
    assert state.last_extraction_outcome == "zero_candidates" and state.backoff_level == 1
    assert state.next_attempt_at == T0 + timedelta(minutes=60)

    env.now = T0 + timedelta(minutes=10)  # too early: not an unchanged SUCCESS, but not due either
    result = env.check()
    assert result.status == "deferred" and env.extract_calls == 1

    env.now = T0 + timedelta(minutes=61)
    result = env.check()
    assert result.status == "fail" and env.extract_calls == 2
    state = env.state()
    assert state.backoff_level == 2 and state.next_attempt_at == env.now + timedelta(minutes=120)


def test_backoff_is_capped_at_the_cadence_interval(make_env):
    env = make_env(update_frequency="weekly")
    env.zero = True
    for _ in range(9):
        env.now = (env.state().next_attempt_at if env.state() else T0) + timedelta(seconds=1)
        env.check()
    state = env.state()
    assert state.backoff_level == 9
    assert state.next_attempt_at - env.now == timedelta(minutes=1440)


def test_a_changed_page_resets_the_backoff(make_env):
    env = make_env()
    env.zero = True
    env.check()
    env.zero = False
    env.html = PAGE_B
    env.now = T0 + timedelta(minutes=5)  # changed page: not held by the back-off
    result = env.check()
    assert result.status == "ok"
    state = env.state()
    assert state.backoff_level == 0 and state.next_attempt_at is None and state.last_extraction_outcome == "ok"


def test_forced_full_recheck_after_seven_days(make_env):
    env = make_env()
    env.check()
    env.now = T0 + timedelta(days=6)
    assert env.check().status == "unchanged"
    env.now = T0 + timedelta(days=7, minutes=1)
    assert env.check().status == "ok" and env.extract_calls == 2
    assert env.state().last_full_extraction_at == env.now


def test_forced_recheck_is_bounded_by_half_the_freshness_window(make_env):
    env = make_env(freshness_window_days=10)  # min(7 days, 10 / 2) = 5 days
    env.check()
    env.now = T0 + timedelta(days=4)
    assert env.check().status == "unchanged"
    env.now = T0 + timedelta(days=5, minutes=1)
    assert env.check().status == "ok"


def test_with_no_state_row_backoff_is_bootstrapped_from_history(make_env):
    env = make_env(update_frequency="weekly")
    for i in range(136):
        env.db.add(SourceFetchLog(
            source_id=env.source.id, status=FetchStatus.OK, error="listing extraction yielded 0 candidates",
            started_at=T0 - timedelta(hours=i + 1),
        ))
    env.db.commit()
    env.zero = True
    env.check()
    state = env.state()
    assert state.backoff_level >= check_state_repo.MAX_BACKOFF_LEVEL
    assert state.next_attempt_at == T0 + timedelta(minutes=1440)  # the cap: no restart from zero


def test_checks_and_changes_feed_the_noise_window(make_env):
    env = make_env()
    env.check()
    env.html = PAGE_B
    env.now = T0 + timedelta(hours=1)
    env.check()
    state = env.state()
    assert (state.checks_window, state.changed_window) == (2, 1)


# --- conditional requests (FR-SKIP-3) ---------------------------------------------------------


def test_validators_are_not_trusted_and_no_headers_are_sent_when_last_modified_keeps_changing(make_env):
    env = make_env()
    for i in range(3):
        env.response_headers = {"last_modified": f"time-{i}"}  # HEC: equals the response time
        env.now = T0 + timedelta(hours=i + 1)
        env.check()
    assert env.state().validators_trusted is False
    assert all(h is None for h in env.headers_seen)


def test_a_stable_etag_becomes_trusted_and_a_304_skips_extraction(make_env):
    env = make_env()
    env.response_headers = {"etag": '"v1"'}
    env.check()
    assert env.state().validators_trusted is False  # proven only by a second, equal observation
    env.now = T0 + timedelta(hours=1)
    env.check()
    assert env.state().validators_trusted is True
    env.response_headers = {"etag": '"v1"', "304": True}
    env.now = T0 + timedelta(hours=2)
    result = env.check()
    assert env.headers_seen[-1] == {"If-None-Match": '"v1"'}
    assert result.status == "unchanged" and env.extract_calls == 1


def test_a_constant_last_modified_with_an_equal_fingerprint_becomes_trusted(make_env):
    env = make_env()
    env.response_headers = {"last_modified": "Wed, 26 Nov 2026 09:00:00 GMT"}
    env.check()
    env.now = T0 + timedelta(hours=1)
    env.check()
    assert env.state().validators_trusted is True
    env.now = T0 + timedelta(hours=2)
    env.check()
    assert env.headers_seen[-1] == {"If-Modified-Since": "Wed, 26 Nov 2026 09:00:00 GMT"}


def test_a_forced_recheck_sends_no_conditional_headers(make_env):
    env = make_env()
    env.response_headers = {"etag": '"v1"'}
    env.check()
    env.now = T0 + timedelta(hours=1)
    env.check()
    env.now = T0 + timedelta(days=8)
    env.check()
    assert env.headers_seen[-1] is None
    assert env.extract_calls == 2


def test_the_old_one_argument_fetch_seam_still_works(make_env):
    env = make_env()
    calls = []

    def one_arg(url):
        calls.append(url)
        return FetchResult(url=url, success=True, status_code=200, html=PAGE_A, final_url=url)

    result = fetch_and_extract_listing(
        env.db, env.source.id, None, fetch=one_arg, extract_listing=env.extract, bypass_cache=True,
        skip_failing_transition=True, allow_failing=True, sleep=lambda s: None, clock=env.clock,
    )
    assert result.status == "ok" and len(calls) == 1
