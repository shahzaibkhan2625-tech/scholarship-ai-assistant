"""`web_fetch` tool — official-page fetch with explicit failure reporting.
A fetch failure is always reported as a fetch failure, never interpreted as
or presented as "not eligible" (US2 Acceptance Scenario 4)."""

from dataclasses import dataclass

import httpx

_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "ScholarshipAIAssistant/0.1 (+https://example.invalid/bot)"


@dataclass(frozen=True)
class FetchResult:
    url: str
    success: bool
    status_code: int | None = None
    html: str | None = None
    error: str | None = None


def fetch_url(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> FetchResult:
    try:
        response = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except httpx.TimeoutException as exc:
        return FetchResult(url=url, success=False, error=f"Request timed out: {exc}")
    except httpx.RequestError as exc:
        return FetchResult(url=url, success=False, error=f"Could not reach URL: {exc}")

    if response.status_code >= 400:
        return FetchResult(
            url=url,
            success=False,
            status_code=response.status_code,
            error=f"Server responded with HTTP {response.status_code}",
        )

    return FetchResult(url=url, success=True, status_code=response.status_code, html=response.text)
