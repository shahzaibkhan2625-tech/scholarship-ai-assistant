"""003 T289 (FR-ERR-1/2, R-10): a provider failure never reaches the monitor's crash handler as a site failure.

- `_record_crashed_check` records an `LlmError` as a provider outcome with no health transition; any other
  exception keeps today's behaviour (a `[monitor]` FAIL row plus a health failure).
- A whole run with a failing provider leaves the source healthy and its records alone.
- The candidate-validation step stops after the first deferral instead of looping through the rest.
Real (rolled-back) DB; fake fetch and extractor."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmProviderError,
    LlmQuotaExhausted,
    LlmResponseUnparseable,
    LlmTemporarilyUnavailable,
)
from app.data.repositories import monitoring_repo, scholarship_repo, source_repo
from app.models.monitoring import MonitoringTrigger
from app.models.source import FetchStatus, SourceFetchLog, SourceStatus
from app.schemas.source import SourceRegistryCreate
from app.tools.web_fetch import FetchResult
from app.workflows.source_monitor import graph
from app.workflows.source_monitor.graph import run_source_monitor

PAGE = "<html><body><ul><li>Alpha Scholarship 2027</li></ul></body></html>"
RETRY_AT = datetime(2099, 1, 1, tzinfo=timezone.utc)
SECRET = "SECRET-PROVIDER-PAYLOAD"  # allow-secret: test marker proving provider text is never copied


@pytest.fixture
def env(db_session_factory, monkeypatch):
    session = db_session_factory()
    source = source_repo.upsert_source_by_domain(
        session,
        SourceRegistryCreate(
            name="Provider Test", source_type="gov", official_status="official",
            domain=f"prov-{uuid.uuid4().hex[:10]}.example.com", access_method="web", discovery_role=True,
            reliability_level="high", status="active", update_frequency="weekly",
        ),
    )
    session.commit()
    monkeypatch.setattr(
        source_repo, "get_monitorable_sources", lambda db, **kw: [db.get(type(source), source.id)]
    )
    state = {"error": None}

    def fetch(url, headers=None):
        return FetchResult(url=url, success=True, status_code=200, html=PAGE, final_url=url)

    def extract(html, *, source_url):
        raise state["error"]

    class Env:
        pass

    e = Env()
    e.session, e.source, e.state = session, source, state
    e.run = lambda: run_source_monitor(
        session, trigger=MonitoringTrigger.MANUAL, fetch=fetch, extract_listing=extract, sleep=lambda _d: None
    )
    e.rows = lambda: (
        session.expire_all()
        or list(session.execute(select(SourceFetchLog).where(SourceFetchLog.source_id == source.id)).scalars())
    )
    yield e
    session.close()


@pytest.fixture
def health_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(graph.source_repo, "touch_source_check", lambda db, sid, **kw: calls.append(("touch", kw)))
    monkeypatch.setattr(graph, "_apply_health_transition", lambda db, sid, **kw: calls.append(("health", kw)))
    return calls


def _crashed(env, exc):
    run = monitoring_repo.create_run(env.session, MonitoringTrigger.MANUAL)
    graph._record_crashed_check(env.session, run, env.source.id, exc)
    return env.rows()


@pytest.mark.parametrize(
    "error, status, outcome",
    [
        (LlmQuotaExhausted(RETRY_AT), FetchStatus.FAIL, "provider_quota"),
        (LlmProviderError(), FetchStatus.FAIL, "provider_error"),
        (LlmResponseUnparseable(), FetchStatus.FAIL, "provider_parse"),
        (LlmBudgetDeferred(RETRY_AT), FetchStatus.OK, "deferred"),
        (LlmTemporarilyUnavailable(RETRY_AT, "daily_allowance_spent"), FetchStatus.OK, "deferred"),
    ],
)
def test_crashed_check_records_an_llm_error_as_a_provider_outcome_without_a_health_transition(
    env, health_calls, error, status, outcome
):
    rows = _crashed(env, error)
    assert len(rows) == 1
    assert rows[0].status == status and rows[0].outcome == outcome
    assert "[monitor]" not in (rows[0].error or "")
    assert health_calls == []  # neither the check timestamps nor the health transition moved


def test_crashed_check_keeps_provider_text_out_of_the_row(env, health_calls):
    error = LlmProviderError()
    error.args = (SECRET,)
    rows = _crashed(env, error)
    assert SECRET not in (rows[0].error or "") and len(rows[0].error or "") <= 120


def test_crashed_check_keeps_todays_behaviour_for_any_other_exception(env, health_calls):
    rows = _crashed(env, RuntimeError("boom"))
    assert len(rows) == 1 and rows[0].status == FetchStatus.FAIL and rows[0].outcome is None
    assert rows[0].error == "[monitor] RuntimeError: boom"
    assert health_calls == [("touch", {"success": False}), ("health", {"success": False})]


@pytest.mark.parametrize("error", [LlmQuotaExhausted(RETRY_AT), LlmProviderError(), LlmResponseUnparseable()])
def test_a_run_with_a_failing_provider_leaves_the_source_healthy_and_the_records_alone(env, error):
    env.state["error"] = error
    for _ in range(settings_health_window() + 1):  # more runs than the health window would tolerate
        run = env.run()
        assert run.sources_failed == 0
    env.session.expire_all()
    assert source_repo.get_source_by_id(env.session, env.source.id).status == SourceStatus.ACTIVE
    assert source_repo.consecutive_failure_count(env.session, env.source.id) == 0
    assert scholarship_repo.list_for_source(env.session, env.source.id) == []
    assert not any((r.error or "").startswith("[monitor]") for r in env.rows())
    assert {r.outcome for r in env.rows() if r.outcome} == {error_outcome(error)}


def settings_health_window() -> int:
    from app.core.config import settings

    return settings.health_window_n


def error_outcome(error) -> str:
    return {
        LlmQuotaExhausted: "provider_quota",
        LlmProviderError: "provider_error",
        LlmResponseUnparseable: "provider_parse",
    }[type(error)]


# -- the candidate-validation step ------------------------------------------------------------------


@pytest.fixture
def three_candidates(monkeypatch):
    ids = [uuid.uuid4() for _ in range(3)]
    monkeypatch.setattr(
        graph.candidate_validation_repo, "list_pending_needing_validation", lambda db, **kw: list(ids)
    )
    return ids


def _step(env, validate):
    graph._validate_candidates_step(env.session, validate, lambda: datetime.now(timezone.utc))


def _recording(result_for):
    calls = []

    def validate(db, candidate_id):
        calls.append(candidate_id)
        return result_for(len(calls))

    validate.calls = calls
    return validate


@pytest.mark.parametrize("outcome", ["deferred", "provider_quota", "provider_error"])
def test_the_step_stops_after_the_first_deferral(env, three_candidates, outcome):
    validate = _recording(lambda n: SimpleNamespace(provider_outcome=outcome))
    _step(env, validate)
    assert validate.calls == three_candidates[:1]


def test_the_step_stops_when_the_validator_raises_an_llm_error(env, three_candidates):
    def validate(db, candidate_id):
        validate.calls.append(candidate_id)
        raise LlmQuotaExhausted(RETRY_AT)

    validate.calls = []
    _step(env, validate)
    assert validate.calls == three_candidates[:1]


def test_the_step_continues_past_a_page_level_parse_failure_and_ordinary_results(env, three_candidates):
    validate = _recording(lambda n: SimpleNamespace(provider_outcome="provider_parse" if n == 1 else None))
    _step(env, validate)
    assert validate.calls == three_candidates


def test_the_step_still_continues_past_an_ordinary_exception(env, three_candidates):
    def validate(db, candidate_id):
        validate.calls.append(candidate_id)
        raise RuntimeError("bad candidate")

    validate.calls = []
    _step(env, validate)
    assert validate.calls == three_candidates
