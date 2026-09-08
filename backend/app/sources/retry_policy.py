"""Bounded retry policy for source fetches (Blueprint §33: "Source unavailable
/ timeout" → retry per policy with capped backoff, mark source `failing`
after N). Pure — this module performs no I/O itself (`sleep` is injectable
for tests); connectors (T073) call it and decide what "retryable" means for
their transport.
"""

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 2.0
DEFAULT_MAX_DELAY_SECONDS = 8.0


@dataclass(frozen=True)
class RetryOutcome:
    """`attempts` is the total number of tries made (>=1 once the policy has
    run). `succeeded` distinguishes "worked, possibly after retrying" from
    "exhausted retries" — callers must check this rather than inferring it
    from `attempts` alone."""

    succeeded: bool
    attempts: int
    result: T | None = None
    last_error: Exception | None = None

    @property
    def retried(self) -> bool:
        return self.succeeded and self.attempts > 1

    @property
    def exhausted(self) -> bool:
        return not self.succeeded


def run_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    max_delay: float = DEFAULT_MAX_DELAY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    is_retryable: Callable[[Exception], bool] = lambda exc: True,
) -> RetryOutcome:
    """Calls `fn` up to `max_attempts` times with capped exponential backoff
    (`base_delay * 2**(attempt-1)`, never exceeding `max_delay`) between
    attempts. Any exception raised by `fn` is treated as a failed attempt
    unless `is_retryable` says otherwise, in which case it stops immediately."""

    last_error: Exception | None = None
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - caller decides retryability
            last_error = exc
            if attempt >= max_attempts or not is_retryable(exc):
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            sleep(delay)
            continue
        return RetryOutcome(succeeded=True, attempts=attempt, result=result)

    return RetryOutcome(succeeded=False, attempts=attempt, last_error=last_error)
