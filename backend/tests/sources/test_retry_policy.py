"""Retry policy unit tests (T072, Blueprint §33) — pure, no DB/network."""

from app.sources.retry_policy import run_with_retry


def _fake_sleep(calls: list[float]):
    def sleep(delay: float) -> None:
        calls.append(delay)

    return sleep


def test_succeeds_within_n_attempts():
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("transient failure")
        return "ok"

    sleeps: list[float] = []
    outcome = run_with_retry(flaky, max_attempts=3, base_delay=0.01, sleep=_fake_sleep(sleeps))

    assert outcome.succeeded is True
    assert outcome.attempts == 3
    assert outcome.result == "ok"
    assert outcome.retried is True
    assert outcome.exhausted is False
    assert len(sleeps) == 2  # slept between attempt 1->2 and 2->3, not after success


def test_exhausts_and_reports_failure():
    def always_fails() -> str:
        raise RuntimeError("permanent failure")

    sleeps: list[float] = []
    outcome = run_with_retry(always_fails, max_attempts=3, base_delay=0.01, sleep=_fake_sleep(sleeps))

    assert outcome.succeeded is False
    assert outcome.exhausted is True
    assert outcome.attempts == 3
    assert isinstance(outcome.last_error, RuntimeError)
    assert len(sleeps) == 2  # never sleeps after the final (3rd) attempt


def test_backoff_timing_is_capped_not_unbounded():
    def always_fails() -> str:
        raise RuntimeError("permanent failure")

    sleeps: list[float] = []
    run_with_retry(
        always_fails,
        max_attempts=5,
        base_delay=2.0,
        max_delay=6.0,
        sleep=_fake_sleep(sleeps),
    )

    # Uncapped exponential would be [2, 4, 8, 16]; the cap clips it at 6.
    assert sleeps == [2.0, 4.0, 6.0, 6.0]
    assert max(sleeps) <= 6.0


def test_non_retryable_error_stops_immediately():
    attempts = {"count": 0}

    def fails_once() -> str:
        attempts["count"] += 1
        raise ValueError("do not retry me")

    sleeps: list[float] = []
    outcome = run_with_retry(
        fails_once,
        max_attempts=5,
        base_delay=0.01,
        sleep=_fake_sleep(sleeps),
        is_retryable=lambda exc: not isinstance(exc, ValueError),
    )

    assert outcome.succeeded is False
    assert outcome.attempts == 1
    assert attempts["count"] == 1
    assert sleeps == []
