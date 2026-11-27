"""LLM budget invariants eval case (003 T308; constitution Principle V; ADR-0014).

Simulates one budget day of mixed traffic (students, monitoring, candidate validation, backfill) against the
admission policy `decide`, with an in-memory ledger, a fake clock and a fake provider that reports a quota error
partway through the day. After every request it checks the invariants the feature promises:

  1. the allowed total never goes above the daily allowance;
  2. a background request is admitted only while the remaining allowance stays above the reserve plus the
     class's ladder offset (so the reserve stays available to students);
  3. backfill never exceeds its daily share;
  4. no provider call is made while a provider pause is active (students included).

The score is the fraction of checks that hold (threshold 1.0, zero tolerance, like `matching_correctness`): a
policy change that lets background work eat the reserve, ignore a pause or overrun the allowance fails CI. No
database, no network and no live provider call: the free-tier quota is never spent by this eval.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.llm_gate import Priority, RequestType
from app.services import llm_budget

_START = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)  # 00:00 in Los Angeles: the start of a budget day
_PAUSE = timedelta(minutes=15)
_QUOTA_ERROR_AT_CALL = 11  # the provider reports a 429 on its 11th call of the day (a shared key used elsewhere)

# One request every 5 minutes through the 24-hour day (288 slots), in a fixed pattern that exercises every class.
_PATTERN = (
    Priority.MONITORING,
    Priority.USER_FACING,
    Priority.VALIDATION,
    Priority.BACKFILL,
    Priority.USER_FACING,
    Priority.MONITORING,
    Priority.BACKFILL,
    Priority.MONITORING,
)


@dataclass
class SimulationResult:
    checks: int = 0
    passed: int = 0
    failures: list[str] = field(default_factory=list)
    requests_by_class: Counter = field(default_factory=Counter)
    provider_calls: int = 0
    refused_background: int = 0
    pauses: int = 0

    def check(self, ok: bool, label: str) -> None:
        self.checks += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(label)


def simulate() -> SimulationResult:
    config = llm_budget.BudgetConfig.from_settings(settings)
    request_type = RequestType.GENERATE
    allowance = config.allowance[request_type]
    result = SimulationResult()
    allowed_by_class: Counter = Counter()
    paused_until: datetime | None = None
    now = _START

    for step in range(24 * 12):
        priority = _PATTERN[step % len(_PATTERN)]
        now = _START + timedelta(minutes=5 * step)
        result.requests_by_class[priority.value] += 1
        allowed_total = sum(allowed_by_class.values())
        decision = llm_budget.decide(
            config,
            request_type,
            priority,
            allowed_total=allowed_total,
            backfill_allowed=allowed_by_class[Priority.BACKFILL.value],
            paused_until=paused_until,
            now=now,
        )
        if not decision.admit:
            if priority is not Priority.USER_FACING:
                result.refused_background += 1
            continue

        # the request is admitted: it counts, and the provider is called
        remaining_before = allowance - allowed_total
        result.check(paused_until is None or paused_until <= now, f"step {step}: provider call during a pause")
        result.check(remaining_before > 0, f"step {step}: admitted with no allowance left")
        if priority is not Priority.USER_FACING:
            floor = config.reserve + config.offsets[priority]
            result.check(remaining_before > floor, f"step {step}: {priority.value} admitted at or below its floor")
        if priority is Priority.BACKFILL:
            result.check(
                allowed_by_class[priority.value] < config.backfill_share, f"step {step}: backfill above its share"
            )
        allowed_by_class[priority.value] += 1
        result.provider_calls += 1
        result.check(sum(allowed_by_class.values()) <= allowance, f"step {step}: allowed total above the allowance")
        if result.provider_calls == _QUOTA_ERROR_AT_CALL:
            paused_until = now + _PAUSE  # the provider's own 429 pauses calls for a while
            result.pauses += 1

    # the reserve: background work alone can never spend the last `reserve` requests of the day
    background_spent = sum(n for cls, n in allowed_by_class.items() if cls != Priority.USER_FACING.value)
    result.check(background_spent <= allowance - config.reserve, "background work spent into the reserve")
    return result


def score() -> float:
    result = simulate()
    return result.passed / result.checks if result.checks else 0.0
