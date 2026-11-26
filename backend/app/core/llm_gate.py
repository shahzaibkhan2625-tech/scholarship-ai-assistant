"""The LLM budget-gate interface (003, ADR-0014).

`core` defines only the interface: priority classes, request types, the context variable that
carries the current class, the refusal/failure exceptions, and the gate registry. The database-backed
gate lives in `app.services.llm_budget` and is installed at application start, in the scheduler
process and in CLI entry points. Nothing here imports database, model or service code, so the layering
stays acyclic. With no gate installed every call is refused (fail closed).

Exception text is a reason class only; provider payloads, page content and secrets never go into one
(FR-ERR-4).
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from enum import StrEnum
from typing import Protocol, TypeVar


class Priority(StrEnum):
    USER_FACING = "user_facing"
    BACKFILL = "backfill"
    MONITORING = "monitoring"
    VALIDATION = "validation"


class RequestType(StrEnum):
    GENERATE = "generate"
    EMBED = "embed"


class LlmError(Exception):
    """Base of every LLM-side failure or refusal."""


class LlmPriorityMissing(LlmError):
    """A provider call was made outside any `llm_priority` scope (FR-BUDGET-3)."""

    def __init__(self) -> None:
        super().__init__("priority_missing")


class LlmGateNotInstalled(LlmError):
    """No gate is installed: the call is refused (fail closed)."""

    def __init__(self) -> None:
        super().__init__("gate_not_installed")


class LlmBudgetDeferred(LlmError):
    """A background request was refused for now; the work is deferred, never failed."""

    def __init__(self, retry_at: datetime) -> None:
        super().__init__("budget_deferred")
        self.retry_at = retry_at


TEMPORARILY_UNAVAILABLE_REASONS = ("daily_allowance_spent", "provider_quota", "provider_unavailable")


class LlmTemporarilyUnavailable(LlmError):
    """A user-facing request was refused; the route answers 503 with `Retry-After`."""

    def __init__(self, retry_at: datetime, reason: str) -> None:
        if reason not in TEMPORARILY_UNAVAILABLE_REASONS:
            raise ValueError(f"unknown reason: {reason}")
        super().__init__(reason)
        self.retry_at = retry_at
        self.reason = reason


class LlmQuotaExhausted(LlmError):
    """The provider reported quota exhaustion (HTTP 429)."""

    def __init__(
        self, retry_at: datetime | None = None, daily: bool = False, observed_limit: int | None = None
    ) -> None:
        super().__init__("provider_quota")
        self.retry_at = retry_at
        self.daily = daily
        self.observed_limit = observed_limit


class LlmProviderError(LlmError):
    """Outage, network failure or any other provider-side error."""

    def __init__(self) -> None:
        super().__init__("provider_error")


class LlmResponseUnparseable(LlmError):
    """The provider answered but the reply could not be used."""

    def __init__(self) -> None:
        super().__init__("provider_parse")


class LlmGate(Protocol):
    def acquire(self, request_type: RequestType, priority: Priority) -> None:
        """Reserve one request or raise `LlmBudgetDeferred` / `LlmTemporarilyUnavailable`."""

    def report_provider_error(self, request_type: RequestType, error: LlmError) -> None:
        """Record a provider-side failure (a 429 sets the pause)."""


_priority: ContextVar[Priority | None] = ContextVar("llm_priority", default=None)


def current_priority() -> Priority | None:
    return _priority.get()


def require_priority() -> Priority:
    priority = _priority.get()
    if priority is None:
        raise LlmPriorityMissing()
    return priority


@contextmanager
def llm_priority(priority: Priority) -> Iterator[None]:
    """Declare the class of every provider call made inside the block; scopes nest and restore."""
    token = _priority.set(priority)
    try:
        yield
    finally:
        _priority.reset(token)


_T = TypeVar("_T")


def run_as_backfill(func: Callable[..., _T], *args, **kwargs) -> _T:
    """Run a one-off job under the `backfill` class (for later features, e.g. 004 re-extraction)."""
    with llm_priority(Priority.BACKFILL):
        return func(*args, **kwargs)


class _ClosedGate:
    def acquire(self, request_type: RequestType, priority: Priority) -> None:
        raise LlmGateNotInstalled()

    def report_provider_error(self, request_type: RequestType, error: LlmError) -> None:
        return None


_CLOSED = _ClosedGate()
_installed: LlmGate | None = None


def install_gate(gate: LlmGate | None) -> None:
    global _installed
    _installed = gate


def get_gate() -> LlmGate:
    return _installed if _installed is not None else _CLOSED
