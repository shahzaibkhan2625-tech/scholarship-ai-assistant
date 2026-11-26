"""003 T287 (FR-ERR-1..4, R-10): a provider failure during extraction is recorded, never treated as a site
failure. Real (rolled-back) DB; fake fetch, fake clock, fake extractor that raises the `LlmError` under test."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.llm import LlmNotConfiguredError
from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmError,
    LlmProviderError,
    LlmQuotaExhausted,
    LlmResponseUnparseable,
    LlmTemporarilyUnavailable,
)
from app.data.repositories import check_state_repo, source_repo
from app.models.source import FetchStatus, SourceFetchLog, SourceStatus
from app.schemas.source import SourceRegistryCreate
from app.sources.connectors.official_fetch import fetch_and_extract_listing
from app.tools import listing_fetch as listing_fetch_module
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.web_fetch import FetchResult

T0 = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
RETRY_AT = T0 + timedelta(hours=2)
PAGE = "<html><body><ul><li>Alpha Scholarship</li><li>Beta Grant</li></ul></body></html>"
SECRET_TEXT = "SECRET-PROVIDER-PAYLOAD sk-123"  # allow-secret: test marker proving provider text is never copied


class Env:
    def __init__(self, db, source):
        self.db, self.source = db, source
        self.now = T0
        self.error: Exception | None = None
        self.extract_calls = 0

    def clock(self):
        return self.now

    def fetch(self, url, headers=None):
        return FetchResult(url=url, success=True, status_code=200, html=PAGE, final_url=url)

    def extract(self, html, *, source_url):
        self.extract_calls += 1
        if self.error is not None:
            raise self.error
        return ExtractListingOutput(
            candidates=[ExtractedListingCandidate(name="Alpha Scholarship")], accepted_count=1, rejected_count=0
        )

    def check(self, **kwargs):
        options = dict(bypass_cache=True, skip_failing_transition=True, allow_failing=True)
        options.update(kwargs)
        return fetch_and_extract_listing(
            self.db, self.source.id, None, fetch=self.fetch, extract_listing=self.extract,
            sleep=lambda s: None, clock=self.clock, **options,
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

    def source_status(self):
        self.db.expire_all()
        return source_repo.get_source_any_status(self.db, self.source.id).status


@pytest.fixture
def make_env(db_session_factory):
    sessions = []

    def _make():
        session = db_session_factory()
        sessions.append(session)
        source = source_repo.upsert_source_by_domain(
            session,
            SourceRegistryCreate(
                name="PE Test", source_type="gov", official_status="official",
                domain=f"pe-{uuid.uuid4().hex[:10]}.example.com", access_method="web", discovery_role=True,
                reliability_level="high", status="active", update_frequency="weekly",
            ),
        )
        session.commit()
        return Env(session, source)

    yield _make
    for session in sessions:
        session.close()


@pytest.fixture
def no_mark_failing(monkeypatch):
    calls = []
    monkeypatch.setattr(source_repo, "mark_source_failing", lambda *a, **k: calls.append(a))
    return calls


# (error raised by the extractor, status of the logged row, outcome, check-state outcome)
FAILURES = [
    pytest.param(LlmQuotaExhausted(RETRY_AT), FetchStatus.FAIL, "provider_quota", "provider_quota", id="quota"),
    pytest.param(LlmProviderError(), FetchStatus.FAIL, "provider_error", "provider_error", id="outage"),
    pytest.param(LlmResponseUnparseable(), FetchStatus.FAIL, "provider_parse", "provider_parse", id="unparseable"),
    pytest.param(LlmNotConfiguredError("GEMINI_API_KEY is not configured"), FetchStatus.FAIL, "provider_error",
                 "provider_error", id="not-configured"),
    pytest.param(LlmBudgetDeferred(RETRY_AT), FetchStatus.OK, "deferred", "deferred", id="budget-deferred"),
    pytest.param(LlmTemporarilyUnavailable(RETRY_AT, "daily_allowance_spent"), FetchStatus.OK, "deferred", "deferred",
                 id="allowance-spent-user-facing"),
]


@pytest.mark.parametrize("error, row_status, outcome, state_outcome", FAILURES)
def test_a_provider_failure_becomes_a_deferred_result_with_an_outcome_row(
    make_env, no_mark_failing, error, row_status, outcome, state_outcome
):
    env = make_env()
    env.error = error
    result = env.check()

    assert result.status == "deferred" and result.candidates == [] and result.error is None
    assert result.retry_at is not None and result.retry_at > T0
    rows = [r for r in env.rows() if r.outcome is not None]
    assert len(rows) == 1
    assert rows[0].status == row_status and rows[0].outcome == outcome and rows[0].items_found is None
    assert no_mark_failing == []  # never marks the source failing
    assert env.source_status() == SourceStatus.ACTIVE

    state = env.state()
    assert state.fingerprint is None and state.fingerprint_at is None  # not stored: retried against the old baseline
    assert state.last_extraction_outcome == state_outcome
    assert state.next_attempt_at == result.retry_at


def test_a_quota_failure_uses_the_provider_retry_time(make_env):
    env = make_env()
    env.error = LlmQuotaExhausted(RETRY_AT)
    assert env.check().retry_at == RETRY_AT


def test_a_failure_without_a_provider_time_retries_after_the_configured_unavailable_seconds(make_env):
    from app.core.config import settings

    env = make_env()
    env.error = LlmProviderError()
    result = env.check()
    assert result.retry_at == T0 + timedelta(seconds=settings.llm_unavailable_retry_seconds)


def test_a_provider_failure_keeps_the_old_baseline_and_changes_no_scholarship(make_env, no_mark_failing):
    env = make_env()
    assert env.check().status == "ok"
    before = env.state()
    old_fingerprint, old_full = before.fingerprint, before.last_full_extraction_at
    env.error = LlmQuotaExhausted(RETRY_AT)
    env.now = T0 + timedelta(days=8)  # forced re-check is due, so the extractor is reached
    result = env.check()
    assert result.status == "deferred" and result.candidates == []
    after = env.state()
    assert after.fingerprint == old_fingerprint and after.last_full_extraction_at == old_full
    assert env.source_status() == SourceStatus.ACTIVE and no_mark_failing == []


def test_the_extraction_cache_never_answers_from_a_provider_failure_row(make_env):
    env = make_env()
    env.error = LlmProviderError()
    env.check()
    env.error = None
    env.now = T0 + timedelta(hours=1)
    assert env.check(bypass_cache=False).status == "ok"  # the failure row is not a cached success
    assert env.extract_calls == 2


def test_recorded_reasons_are_short_and_carry_no_provider_text(make_env):
    env = make_env()
    error = LlmProviderError()
    error.args = (SECRET_TEXT,)
    env.error = error
    env.check()
    for row in env.rows():
        assert SECRET_TEXT not in (row.error or "") and "sk-123" not in (row.error or "")
        assert PAGE not in (row.error or "") and len(row.error or "") <= 120


def test_the_discovery_path_gets_the_same_deferred_result(make_env, monkeypatch, no_mark_failing):
    env = make_env()
    env.error = LlmQuotaExhausted(RETRY_AT)

    def _through_the_connector(db, source_id, url=None):
        return fetch_and_extract_listing(
            db, source_id, url, fetch=env.fetch, extract_listing=env.extract, sleep=lambda s: None, clock=env.clock
        )

    monkeypatch.setattr(listing_fetch_module, "fetch_and_extract_listing", _through_the_connector)
    out = listing_fetch_module.listing_fetch_tool(env.db, env.source.id)

    assert out.status == "deferred" and out.error is None and out.candidates == []  # never "no scholarships found"
    assert [r.outcome for r in env.rows() if r.outcome] == ["provider_quota"]
    assert no_mark_failing == [] and env.state().fingerprint is None


def test_an_error_that_is_not_an_llm_error_still_propagates(make_env):
    env = make_env()
    env.error = RuntimeError("bug")
    with pytest.raises(RuntimeError):
        env.check()


def test_a_missing_priority_is_a_bug_and_still_propagates(make_env):
    from app.core.llm_gate import LlmPriorityMissing

    env = make_env()
    env.error = LlmPriorityMissing()
    with pytest.raises(LlmPriorityMissing):
        env.check()
