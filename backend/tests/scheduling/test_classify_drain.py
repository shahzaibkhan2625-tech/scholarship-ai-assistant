"""003 T471 (FR-BUDGET-13, FR-BUDGET-10, SC-016): the scheduler tick drains the pending-classification queue.

The drain runs under the `backfill` class, takes the oldest rows first, leaves rows when the gate refuses,
re-runs classify on the record's text, fills only dimensions that are still unknown, deletes the row after an
answer, counts failed attempts, logs each attempt without text, and never takes allowance from the user-facing
reserve. Real (rolled-back) DB; fake clock, fake gate and fake provider client, no network."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from google.genai import errors as genai_errors
from sqlalchemy import select

from app.core import llm, llm_gate
from app.core.llm_gate import LlmBudgetDeferred, Priority, RequestType
from app.data.repositories import classify_pending_repo as queue
from app.data.repositories import llm_budget_repo
from app.models.classify_pending import ClassifyPending
from app.models.scholarship import DegreeLevel, FundingStatus, Scholarship, ScholarshipField
from app.scheduling import scheduler as sched_mod
from app.services import classify_backfill
from app.services.llm_budget import BudgetConfig, DbLlmGate

T0 = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
RETRY_AT = datetime(2099, 1, 1, tzinfo=timezone.utc)
LA = ZoneInfo("America/Los_Angeles")


class Gate:
    def __init__(self, refuse: Exception | None = None) -> None:
        self.refuse, self.priorities = refuse, []

    def acquire(self, request_type, priority):
        self.priorities.append(priority)
        if self.refuse is not None:
            raise self.refuse

    def report_provider_error(self, request_type, error):
        return None


class Models:
    """Answers every call with `text`, or raises `error`; records the prompt of each call."""

    def __init__(self, text: str = "{}", error: BaseException | None = None) -> None:
        self.text, self.error, self.prompts = text, error, []

    def generate_content(self, **kwargs):
        self.prompts.append(kwargs["contents"])
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text)


@pytest.fixture
def provider(monkeypatch):
    def _install(gate=None, models=None):
        gate, models = gate or Gate(), models or Models()
        llm_gate.install_gate(gate)
        monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=models))
        return gate, models

    yield _install
    llm_gate.install_gate(None)


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


def _queued(db, label, minutes=0, **columns):
    row = Scholarship(
        name=f"Drain {label} {uuid.uuid4().hex[:6]}", official_scholarship_url="https://x.example.com",
        notes="Quux details", **columns,
    )
    db.add(row)
    db.flush()
    entry = queue.enqueue(db, row.id, "budget_deferred")
    entry.queued_at = T0 - timedelta(hours=10) + timedelta(minutes=minutes)
    db.flush()
    return row


def _drain(db, limit=10):
    return classify_backfill.drain_classify_pending(db, now=T0, limit=limit)


def _entry(db, scholarship):
    db.expire_all()
    return db.get(ClassifyPending, scholarship.id)


def _provider_type(db, scholarship):
    db.expire_all()
    rows = db.execute(
        select(ScholarshipField).where(ScholarshipField.scholarship_id == scholarship.id, ScholarshipField.key == "provider_type")
    ).scalars().all()
    return rows


# -- order, class, refusal -------------------------------------------------------------------------------------


def test_oldest_rows_are_drained_first(db, provider):
    _, models = provider()
    newer, older = _queued(db, "newer", minutes=30), _queued(db, "older", minutes=1)
    _drain(db)
    mine = [p for p in models.prompts if "Drain" in p]
    assert [older.name in p for p in mine[:2]] == [True, False]
    assert newer.name in mine[1]


def test_the_drain_declares_the_backfill_class_itself(db, provider):
    gate, _ = provider()
    _queued(db, "a")
    _drain(db)
    assert gate.priorities and set(gate.priorities) == {Priority.BACKFILL}


def test_a_refusing_gate_leaves_every_row_untouched_and_stops(db, provider):
    gate, models = provider(Gate(LlmBudgetDeferred(RETRY_AT)))
    rows = [_queued(db, str(i), minutes=i) for i in range(3)]
    result = _drain(db)
    assert models.prompts == []  # no provider call
    assert len(gate.priorities) == 1  # asked once, then stopped: the rest would be refused too
    for row in rows:
        entry = _entry(db, row)
        assert entry is not None and entry.attempts == 0 and entry.last_reason == "budget_deferred"
    assert (result.answered, result.failed) == (0, 0)


# -- what is written ---------------------------------------------------------------------------------------------


def test_only_unknown_dimensions_are_filled_and_a_known_value_is_never_overwritten(db, provider):
    provider(models=Models('{"country": "Germany", "degree_level": "PhD", "funding_type": "fully_funded", '
                           '"field": "Physics", "provider_type": "gov"}'))
    record = _queued(db, "partial", country="France", funding_status=FundingStatus.PARTIALLY_FUNDED)
    _drain(db)
    db.expire_all()
    stored = db.get(Scholarship, record.id)
    assert stored.country == "France"  # known: kept
    assert stored.funding_status == FundingStatus.PARTIALLY_FUNDED  # known: kept
    assert stored.degree_level == DegreeLevel.PHD  # unknown: filled
    assert stored.field == "Physics"
    fields = _provider_type(db, record)
    assert len(fields) == 1 and fields[0].value == "gov" and fields[0].confidence == "inferred"


def test_an_existing_provider_type_is_kept(db, provider):
    provider(models=Models('{"provider_type": "university"}'))
    record = _queued(db, "ptype")
    db.add(ScholarshipField(scholarship_id=record.id, key="provider_type", value="gov", value_status="known", confidence="verified"))
    db.flush()
    _drain(db)
    fields = _provider_type(db, record)
    assert [f.value for f in fields] == ["gov"]


def test_a_value_set_after_queueing_survives(db, provider):
    provider(models=Models('{"country": "Germany"}'))
    record = _queued(db, "raced")
    record.country = "Japan"  # set by someone between queueing and the drain
    db.flush()
    _drain(db)
    db.expire_all()
    assert db.get(Scholarship, record.id).country == "Japan"


def test_the_row_is_deleted_once_classify_answered_even_with_nothing_to_fill(db, provider):
    provider(models=Models("{}"))
    record = _queued(db, "empty")
    result = _drain(db)
    assert _entry(db, record) is None
    assert result.answered == 1


# -- failures ---------------------------------------------------------------------------------------------------


def test_a_failure_counts_an_attempt_and_keeps_the_row(db, provider):
    provider(models=Models(error=genai_errors.ServerError(503, {"error": {"code": 503, "message": "SECRET-PAYLOAD"}})))
    first, second = _queued(db, "first", minutes=1), _queued(db, "second", minutes=2)
    result = _drain(db)
    entry = _entry(db, first)
    assert entry.attempts == 1 and entry.last_reason == "provider_error" and entry.last_attempt_at == T0
    assert "SECRET-PAYLOAD" not in (entry.last_reason or "")
    assert _entry(db, second).attempts == 0  # an outage stops the drain; the next row was not tried
    assert result.failed == 1


def test_an_unparseable_reply_counts_an_attempt_and_the_drain_goes_on(db, provider):
    provider(models=Models("this is not json"))
    first, second = _queued(db, "first", minutes=1), _queued(db, "second", minutes=2)
    _drain(db)
    assert [(_entry(db, r).attempts, _entry(db, r).last_reason) for r in (first, second)] == [
        (1, "provider_parse"), (1, "provider_parse")]


def test_the_limit_bounds_the_work_of_one_tick(db, provider):
    _, models = provider()
    for i in range(5):
        _queued(db, str(i), minutes=i)
    _drain(db, limit=2)
    assert len(models.prompts) == 2


# -- logging ----------------------------------------------------------------------------------------------------


def test_each_attempt_is_logged_without_the_record_text(db, provider, caplog):
    provider(models=Models("{}"))
    record = _queued(db, "logged")
    with caplog.at_level(logging.INFO, logger="app.services.classify_backfill"):
        _drain(db)
    attempts = [r for r in caplog.records if r.name == "app.services.classify_backfill" and "attempt" in r.getMessage()]
    assert len(attempts) == 1
    flat = " ".join([attempts[0].getMessage(), *(str(v) for v in vars(attempts[0]).values())])
    assert record.name not in flat and "Quux" not in flat


# -- the user-facing reserve --------------------------------------------------------------------------------------


def test_the_drain_never_dips_into_the_user_facing_reserve(db, db_session_factory):
    config = BudgetConfig(
        allowance={RequestType.GENERATE: 6, RequestType.EMBED: None}, reserve=2,
        offsets={Priority.BACKFILL: 0, Priority.MONITORING: 1, Priority.VALIDATION: 2},
        backfill_share=3, tz=LA, default_pause_seconds=900,
    )
    llm_gate.install_gate(DbLlmGate(config, session_factory=db_session_factory, clock=lambda: T0))
    models = Models("{}")
    import app.core.llm as llm_module

    original = llm_module.get_gemini_client
    llm_module.get_gemini_client = lambda: SimpleNamespace(models=models)
    try:
        for i in range(8):
            _queued(db, str(i), minutes=i)
        _drain(db)
    finally:
        llm_module.get_gemini_client = original
        llm_gate.install_gate(None)
    day = T0.astimezone(LA).date()
    used = llm_budget_repo.count(db_session_factory(), day, "generate", "backfill", "allowed")
    assert used == 3 == len(models.prompts)  # the backfill share, not the whole allowance
    assert 6 - used >= config.reserve  # the reserve is intact
    assert queue.size(db) >= 5  # the rest waits for another day or tick


# -- the scheduler tick ---------------------------------------------------------------------------------------------


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def rollback(self):
        return None


@pytest.fixture
def tick(monkeypatch):
    import app.data.repositories.db as db_mod

    calls = []
    monkeypatch.setattr(db_mod, "SessionLocal", lambda: _Session())
    monkeypatch.setattr("app.workflows.source_monitor.graph.run_source_monitor", lambda s, **kw: calls.append("monitor"))
    monkeypatch.setattr(classify_backfill, "drain_classify_pending", lambda s, **kw: calls.append("drain"))
    return calls


def test_the_tick_runs_the_monitor_and_then_the_drain(tick):
    sched_mod._run_scheduled_monitor()
    assert tick == ["monitor", "drain"]


def test_the_drain_still_runs_when_the_monitor_run_fails(tick, monkeypatch):
    def boom(s, **kw):
        tick.append("monitor")
        raise RuntimeError("run failed")

    monkeypatch.setattr("app.workflows.source_monitor.graph.run_source_monitor", boom)
    sched_mod._run_scheduled_monitor()
    assert tick == ["monitor", "drain"]


def test_a_failing_drain_never_kills_the_tick(tick, monkeypatch):
    def boom(s, **kw):
        raise RuntimeError("drain failed")

    monkeypatch.setattr(classify_backfill, "drain_classify_pending", boom)
    sched_mod._run_scheduled_monitor()  # must not raise
    assert tick == ["monitor"]
