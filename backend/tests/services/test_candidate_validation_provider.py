"""003 T289 (FR-ERR-1, R-10): candidate validation records a provider failure on its advisory result instead of
reporting it as a finding about the page. The fetch / extract / DNS seams are injected; DB-backed tests run
inside the rolled-back `db_session_factory` transaction."""

import pytest

from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmProviderError,
    LlmQuotaExhausted,
    LlmResponseUnparseable,
)
from app.data.repositories import source_repo
from app.schemas.source import CandidateSourceCreate
from app.services import candidate_validation as cv
from app.tools.web_fetch import FetchResult
from datetime import datetime, timezone

RETRY_AT = datetime(2099, 1, 1, tzinfo=timezone.utc)
PAGE = "<html><body><p>The University Foundation offers awards.</p></body></html>"
SECRET = "SECRET-PROVIDER-PAYLOAD"  # allow-secret: test marker proving provider text is never copied


@pytest.fixture
def session(db_session_factory):
    s = db_session_factory()
    yield s
    s.close()


def _validate(session, extract):
    candidate = source_repo.create_candidate_source(
        session, CandidateSourceCreate(url="https://funds.example-uni.edu/scholarships", signals={})
    )
    return cv.validate_candidate(
        session,
        candidate.id,
        fetch=lambda url: FetchResult(url=url, success=True, status_code=200, html=PAGE, final_url=url),
        extract_listing=extract,
        resolve=lambda host: ["93.184.216.34"],
    )


def _raising(error):
    def extract(html, *, source_url):
        raise error

    return extract


@pytest.mark.parametrize(
    "error, outcome",
    [
        (LlmQuotaExhausted(RETRY_AT), "provider_quota"),
        (LlmProviderError(), "provider_error"),
        (LlmResponseUnparseable(), "provider_parse"),
        (LlmBudgetDeferred(RETRY_AT), "deferred"),
    ],
)
def test_a_provider_failure_is_recorded_as_such_on_the_advisory_result(session, error, outcome):
    row = _validate(session, _raising(error))
    assert row.reachable is True  # the page itself was fine
    assert row.extractable is False
    assert outcome in row.extractable_detail and "extraction raised" not in row.extractable_detail
    assert row.provider_outcome == outcome  # the in-memory signal the monitor step reads


def test_provider_text_never_reaches_the_advisory_row(session):
    error = LlmProviderError()
    error.args = (SECRET,)
    row = _validate(session, _raising(error))
    assert SECRET not in row.extractable_detail


def test_any_other_extraction_error_keeps_todays_detail_and_has_no_provider_outcome(session):
    row = _validate(session, _raising(RuntimeError("boom")))
    assert row.extractable is False
    assert row.extractable_detail == "extraction raised RuntimeError: boom"
    assert getattr(row, "provider_outcome", None) is None


def test_a_normal_validation_has_no_provider_outcome(session):
    from app.tools.extract_listing import ExtractListingOutput

    row = _validate(
        session, lambda html, *, source_url: ExtractListingOutput(candidates=[], accepted_count=2, rejected_count=0)
    )
    assert row.extractable is True and getattr(row, "provider_outcome", None) is None
