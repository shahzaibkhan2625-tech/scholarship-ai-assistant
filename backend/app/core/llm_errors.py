"""Translate a provider error into the `LlmError` family (003, research R-03). Pure: no I/O, no
database. The returned exception carries a reason class and a retry time only; provider text
(message, details, payload) is never copied into it (FR-ERR-4)."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.genai import errors as genai_errors

from app.core.budget_day import next_boundary
from app.core.llm_gate import LlmError, LlmProviderError, LlmQuotaExhausted

_MIN_RETRY_SECONDS = 60
_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")


def _details(error: genai_errors.APIError) -> list[dict]:
    payload = error.details if isinstance(error.details, dict) else {}
    body = payload.get("error", payload)
    items = body.get("details", []) if isinstance(body, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _violations(details: list[dict]) -> list[dict]:
    found: list[dict] = []
    for item in details:
        if str(item.get("@type", "")).endswith("QuotaFailure"):
            found.extend(v for v in item.get("violations", []) if isinstance(v, dict))
    return found


def _retry_delay_seconds(details: list[dict]) -> float | None:
    for item in details:
        if str(item.get("@type", "")).endswith("RetryInfo"):
            match = _DELAY.match(str(item.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _observed_limit(violations: list[dict]) -> int | None:
    for violation in violations:
        value = str(violation.get("quotaValue", ""))
        if value.isdigit():
            return int(value)
    return None


def _is_daily(error: genai_errors.APIError, violations: list[dict]) -> bool:
    if any("perday" in str(v.get("quotaId", "")).lower().replace("_", "") for v in violations):
        return True
    return "per day" in str(error.message or "").lower()


def classify_provider_error(
    exc: BaseException, *, now: datetime, tz: ZoneInfo, default_pause_seconds: int
) -> LlmError:
    if not isinstance(exc, genai_errors.APIError) or exc.code != 429:
        return LlmProviderError()
    details = _details(exc)
    violations = _violations(details)
    limit = _observed_limit(violations)
    if _is_daily(exc, violations):
        return LlmQuotaExhausted(next_boundary(now, tz), daily=True, observed_limit=limit)
    delay = _retry_delay_seconds(details)
    seconds = default_pause_seconds if delay is None else max(delay, _MIN_RETRY_SECONDS)
    return LlmQuotaExhausted(now + timedelta(seconds=seconds), daily=False, observed_limit=limit)
