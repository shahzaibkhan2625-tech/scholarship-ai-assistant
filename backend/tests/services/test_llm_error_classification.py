"""003 T265: provider-error classification. The daily-quota fixture reproduces the shape recorded for
the 2026-10-24 HEC failure (research R-03): code 429, RESOURCE_EXHAUSTED, `RetryInfo` 340 s and a
`QuotaFailure` whose quota id contains `PerDay`."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from google.genai import errors as genai_errors

from app.core.llm_errors import classify_provider_error
from app.core.llm_gate import LlmProviderError, LlmQuotaExhausted
from app.services.llm_budget import next_boundary

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 24, 6, 54, tzinfo=timezone.utc)
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "llm" / "quota_error_daily.json"


def _daily() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _classify(payload: dict, code: int = 429):
    return classify_provider_error(
        genai_errors.ClientError(code, payload), now=NOW, tz=LA, default_pause_seconds=900
    )


def test_daily_quota_pauses_until_the_next_budget_day_not_340_seconds():
    error = _classify(_daily())
    assert isinstance(error, LlmQuotaExhausted) and error.daily
    assert error.retry_at == next_boundary(NOW, LA)
    assert error.retry_at > NOW + timedelta(seconds=340)
    assert error.observed_limit == 20


def _per_minute(delay: str | None) -> dict:
    payload = copy.deepcopy(_daily())
    details = payload["error"]["details"]
    details[0]["violations"][0]["quotaId"] = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    details[0]["violations"][0]["quotaValue"] = "5"
    payload["error"]["message"] = "Quota exceeded for requests per minute."
    details[2:] = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}] if delay else []
    return payload


def test_per_minute_violation_pauses_for_the_provider_retry_delay():
    error = _classify(_per_minute("340s"))
    assert not error.daily and error.retry_at == NOW + timedelta(seconds=340)


def test_retry_delay_has_a_minimum_of_sixty_seconds():
    assert _classify(_per_minute("5s")).retry_at == NOW + timedelta(seconds=60)


def test_no_hint_pauses_for_the_default_900_seconds():
    assert _classify(_per_minute(None)).retry_at == NOW + timedelta(seconds=900)


def test_message_saying_per_day_counts_as_daily():
    payload = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded: 20 requests per day."}}
    error = _classify(payload)
    assert error.daily and error.retry_at == next_boundary(NOW, LA)


@pytest.mark.parametrize("code, kind", [(500, genai_errors.ServerError), (503, genai_errors.ServerError),
                                         (400, genai_errors.ClientError), (404, genai_errors.ClientError)])
def test_other_api_errors_become_provider_errors(code, kind):
    error = classify_provider_error(
        kind(code, {"error": {"code": code, "message": "x", "status": "UNAVAILABLE"}}),
        now=NOW, tz=LA, default_pause_seconds=900,
    )
    assert isinstance(error, LlmProviderError)


def test_network_errors_become_provider_errors():
    assert isinstance(
        classify_provider_error(ConnectionError("boom"), now=NOW, tz=LA, default_pause_seconds=900), LlmProviderError
    )


def test_error_text_is_never_copied_into_the_exception():
    secret_marker = "SECRET-PAYLOAD-TEXT"  # allow-secret: inert marker string
    payload = _daily()
    payload["error"]["message"] = secret_marker
    for error in (_classify(payload), _classify(payload, 500),
                  classify_provider_error(RuntimeError(secret_marker), now=NOW, tz=LA, default_pause_seconds=900)):
        assert secret_marker not in str(error) and secret_marker not in repr(error.args)
        assert secret_marker not in repr(vars(error))
