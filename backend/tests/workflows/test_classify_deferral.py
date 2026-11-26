"""003 T470 (FR-BUDGET-13, SC-016): a classify call that is refused or fails does not lose the record.

The record is stored exactly as today (unresolved dimensions empty, nothing guessed), one `classify_pending`
row says why, the record and the row are written in one transaction, and `classify` reports the refusal
instead of hiding it. Entry points: the discovery agent (the `POST /discovery` path) and the monitoring run.
Real (rolled-back) DB; fake gate and fake provider client, no network."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from sqlalchemy import select

from app.agents.discovery.agent import run_discovery
from app.core import llm, llm_gate
from app.core.llm import LlmNotConfiguredError
from app.core.llm_gate import LlmBudgetDeferred, LlmTemporarilyUnavailable, Priority, llm_priority
from app.data.repositories import classify_pending_repo, source_repo
from app.models.classify_pending import ClassifyPending
from app.models.monitoring import MonitoringTrigger
from app.models.scholarship import FundingStatus, Scholarship, ScholarshipField
from app.schemas.source import SourceRegistryCreate
from app.tools.api_connector import ApiConnectorOutput
from app.tools.classify import classify
from app.tools.extract_listing import ExtractedListingCandidate, ExtractListingOutput
from app.tools.listing_fetch import ListingFetchOutput
from app.tools.web_fetch import FetchResult
from app.workflows.ingestion.graph import run_ingestion
from app.workflows.source_monitor.graph import run_source_monitor

RETRY_AT = datetime(2099, 1, 1, tzinfo=timezone.utc)
NAME = "Quux Award"  # no rule in `classify` recognises anything in this text
RAW = {"name": NAME, "deadline": "2027-03-01"}
SECRET = "SECRET-PROVIDER-PAYLOAD"  # allow-secret: test marker proving provider text is never copied


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
    def __init__(self, error: BaseException | None = None, text: str = "{}") -> None:
        self.error, self.text, self.calls = error, text, 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text)


@pytest.fixture
def provider(monkeypatch):
    """provider(gate=..., models=...) installs a fake gate and a fake provider client."""

    def _install(gate: Gate | None = None, models: Models | None = None):
        gate = gate or Gate()
        models = models or Models()
        llm_gate.install_gate(gate)
        monkeypatch.setattr(llm, "get_gemini_client", lambda: SimpleNamespace(models=models))
        return gate, models

    yield _install
    llm_gate.install_gate(None)


# (what goes wrong, gate, models, expected queue reason)
CASES = [
    pytest.param(Gate(LlmTemporarilyUnavailable(RETRY_AT, "daily_allowance_spent")), None, "budget_deferred", id="spent"),
    pytest.param(Gate(LlmBudgetDeferred(RETRY_AT)), None, "budget_deferred", id="deferred"),
    pytest.param(None, Models(genai_errors.ClientError(429, {"error": {"code": 429, "message": SECRET}})),
                 "provider_quota", id="quota"),
    pytest.param(None, Models(genai_errors.ServerError(503, {"error": {"code": 503, "message": SECRET}})),
                 "provider_error", id="outage"),
    pytest.param(None, Models(text="this is not json"), "provider_parse", id="unparseable"),
]


@pytest.fixture
def db(db_session_factory):
    session = db_session_factory()
    yield session
    session.close()


def _source(session, **overrides):
    fields = dict(
        name="Deferral Test", source_type="api", official_status="official",
        domain=f"deferral-{uuid.uuid4().hex[:10]}.example.com", access_method="api", reliability_level="high",
        status="active", discovery_role=True, country="Germany",
    )
    fields.update(overrides)
    return source_repo.upsert_source_by_domain(session, SourceRegistryCreate(**fields))


def _stored(session, name=NAME):
    session.expire_all()
    return session.execute(select(Scholarship).where(Scholarship.name == name)).scalars().all()


def _queue_rows(session):
    session.expire_all()
    return session.execute(select(ClassifyPending)).scalars().all()


def _assert_stored_as_unresolved(session, row):
    assert row.degree_level is None and row.country is None and row.field is None
    assert row.funding_status == FundingStatus.UNKNOWN
    fields = session.execute(select(ScholarshipField).where(ScholarshipField.scholarship_id == row.id)).scalars().all()
    assert not [f for f in fields if f.key == "provider_type"]  # nothing guessed


# -- the classify tool reports the refusal ------------------------------------------------------------------


@pytest.mark.parametrize("gate, models, reason", CASES)
def test_classify_reports_a_refused_or_failed_llm_step(provider, gate, models, reason):
    provider(gate, models)
    with llm_priority(Priority.USER_FACING):
        result = classify(NAME)
    assert result.deferred is True and result.deferred_reason == reason
    assert result.degree_level.value is None and result.country.value is None  # nothing guessed
    assert SECRET not in repr(result)


def test_classify_reports_not_configured(provider, monkeypatch):
    provider()
    monkeypatch.setattr(llm, "get_gemini_client", lambda: (_ for _ in ()).throw(LlmNotConfiguredError("no key")))
    with llm_priority(Priority.USER_FACING):
        result = classify(NAME)
    assert (result.deferred, result.deferred_reason) == (True, "not_configured")


def test_classify_keeps_the_rules_result_when_the_llm_step_is_refused(provider):
    provider(Gate(LlmBudgetDeferred(RETRY_AT)))
    with llm_priority(Priority.USER_FACING):
        result = classify("A PhD scholarship at a university")
    assert result.degree_level.value == "PhD" and result.deferred is True


def test_a_successful_classify_is_not_deferred(provider):
    provider(models=Models(text='{"country": "Germany"}'))
    with llm_priority(Priority.USER_FACING):
        result = classify(NAME)
    assert result.deferred is False and result.deferred_reason is None
    assert result.country.value == "Germany" and result.country.confidence == "inferred"


# -- discovery path ------------------------------------------------------------------------------------------


def _discover(session, source):
    def fetch_api(db_arg, source_id, query):
        return ApiConnectorOutput(source_id=source_id, query=query, status="ok", records=[dict(RAW)] if query == "Germany" else [])

    def listing(_db, source_id, _url):
        return ListingFetchOutput(source_id=source_id, status="ok", candidates=[])

    profile = SimpleNamespace(target_countries=["Germany"], target_fields=[], target_degree_level=None, nationality=None)
    with llm_priority(Priority.USER_FACING):
        return run_discovery(session, profile, fetch_api=fetch_api, listing_fetch=listing)


@pytest.mark.parametrize("gate, models, reason", CASES)
def test_discovery_stores_the_record_unchanged_and_queues_it(db, provider, gate, models, reason):
    provider(gate, models)
    source = _source(db)
    run = _discover(db, source)
    assert len(run.discovery_result.results) == 1
    rows = _stored(db)
    assert len(rows) == 1
    _assert_stored_as_unresolved(db, rows[0])
    queued = _queue_rows(db)
    mine = [q for q in queued if q.scholarship_id == rows[0].id]
    assert len(mine) == 1 and mine[0].last_reason == reason and mine[0].attempts == 0
    assert SECRET not in (mine[0].last_reason or "")


def test_discovery_with_a_working_provider_queues_nothing(db, provider):
    provider(models=Models(text='{"country": "Germany", "degree_level": "PhD"}'))
    source = _source(db)
    _discover(db, source)
    rows = _stored(db)
    assert len(rows) == 1 and rows[0].country == "Germany"
    assert [q for q in _queue_rows(db) if q.scholarship_id == rows[0].id] == []


# -- ingestion: one transaction ---------------------------------------------------------------------------------


def test_the_record_and_its_queue_row_are_written_in_one_transaction(db, provider, monkeypatch):
    provider(Gate(LlmBudgetDeferred(RETRY_AT)))
    source = _source(db)

    def broken_enqueue(*_a, **_k):
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(classify_pending_repo, "enqueue", broken_enqueue)
    with llm_priority(Priority.USER_FACING):
        state = run_ingestion(db, source_id=source.id, source_url=f"https://{source.domain}", raw=dict(RAW))
    assert state.get("error_stage") == "store"
    assert _stored(db) == []  # no record without its queue row, and no queue row without its record


def test_a_duplicate_of_a_stored_record_queues_nothing_new(db, provider):
    provider(Gate(LlmBudgetDeferred(RETRY_AT)))
    source = _source(db)
    with llm_priority(Priority.USER_FACING):
        run_ingestion(db, source_id=source.id, source_url=f"https://{source.domain}", raw=dict(RAW))
    before = len(_queue_rows(db))
    with llm_priority(Priority.USER_FACING):
        run_ingestion(db, source_id=source.id, source_url=f"https://{source.domain}", raw=dict(RAW))
    assert len(_queue_rows(db)) == before  # idempotent per record; a matched duplicate is not a new record


# -- monitoring run -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("gate, models, reason", CASES)
def test_a_monitoring_run_stores_a_new_record_and_queues_it(db, provider, monkeypatch, gate, models, reason):
    provider(gate, models)
    source = _source(db, access_method="web", source_type="gov", update_frequency="weekly")
    db.commit()
    monkeypatch.setattr(source_repo, "get_monitorable_sources", lambda d, **kw: [d.get(type(source), source.id)])
    page = "<html><body><ul><li>Quux Award</li></ul></body></html>"

    def fetch(url, headers=None):
        return FetchResult(url=url, success=True, status_code=200, html=page, final_url=url)

    def extract(html, *, source_url):
        candidate = ExtractedListingCandidate(name=NAME, deadline="2027-03-01")
        return ExtractListingOutput(candidates=[candidate], accepted_count=1, rejected_count=0)

    run_source_monitor(
        db, trigger=MonitoringTrigger.MANUAL, fetch=fetch, extract_listing=extract, sleep=lambda _d: None
    )
    rows = _stored(db)
    assert len(rows) == 1
    _assert_stored_as_unresolved(db, rows[0])
    mine = [q for q in _queue_rows(db) if q.scholarship_id == rows[0].id]
    assert len(mine) == 1 and mine[0].last_reason == reason
