"""SQL for the LLM budget ledger and pause state (003, R-04). No policy lives here.

Scoping: both tables are system-owned (no `user_id`), like `source_registry`.
Callers own the transaction: nothing here commits.
"""

from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.llm_budget import LlmBudgetLedger, LlmBudgetState

ALLOWED = "allowed"


def increment(
    db: Session, budget_day: date, request_type: str, priority_class: str, event: str, by: int = 1
) -> None:
    """Atomic `count = count + by` upsert on the (day, type, class, event) key."""
    stmt = pg_insert(LlmBudgetLedger).values(
        budget_day=budget_day,
        request_type=request_type,
        priority_class=priority_class,
        event=event,
        count=by,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["budget_day", "request_type", "priority_class", "event"],
        set_={"count": LlmBudgetLedger.count + by, "updated_at": func.now()},
    )
    db.execute(stmt)


def count(db: Session, budget_day: date, request_type: str, priority_class: str, event: str) -> int:
    stmt = select(LlmBudgetLedger.count).where(
        LlmBudgetLedger.budget_day == budget_day,
        LlmBudgetLedger.request_type == request_type,
        LlmBudgetLedger.priority_class == priority_class,
        LlmBudgetLedger.event == event,
    )
    return db.execute(stmt).scalar() or 0


def sums_by_class(db: Session, budget_day: date, request_type: str, event: str) -> dict[str, int]:
    stmt = select(LlmBudgetLedger.priority_class, LlmBudgetLedger.count).where(
        LlmBudgetLedger.budget_day == budget_day,
        LlmBudgetLedger.request_type == request_type,
        LlmBudgetLedger.event == event,
    )
    return {row.priority_class: row.count for row in db.execute(stmt)}


def allowed_today(db: Session, budget_day: date, request_type: str) -> int:
    return sum(sums_by_class(db, budget_day, request_type, ALLOWED).values())


def day_counts(db: Session, budget_day: date) -> dict[tuple[str, str, str], int]:
    stmt = select(
        LlmBudgetLedger.request_type, LlmBudgetLedger.priority_class, LlmBudgetLedger.event, LlmBudgetLedger.count
    ).where(LlmBudgetLedger.budget_day == budget_day)
    return {(r.request_type, r.priority_class, r.event): r.count for r in db.execute(stmt)}


def prune_older_than(db: Session, cutoff_day: date) -> int:
    """Delete ledger rows for days before `cutoff_day`; the pause table is untouched."""
    result = db.execute(delete(LlmBudgetLedger).where(LlmBudgetLedger.budget_day < cutoff_day))
    return result.rowcount or 0


def get_pause(db: Session, request_type: str) -> LlmBudgetState | None:
    return db.get(LlmBudgetState, request_type)


def set_pause(
    db: Session,
    request_type: str,
    paused_until: datetime,
    reason: str,
    observed_limit: int | None = None,
) -> None:
    values = {
        "paused_until": paused_until,
        "pause_reason": reason,
        "pause_set_at": datetime.now(timezone.utc),
        "observed_limit": observed_limit,
    }
    stmt = pg_insert(LlmBudgetState).values(request_type=request_type, **values)
    db.execute(stmt.on_conflict_do_update(index_elements=["request_type"], set_=values))
    db.expire_all()


def clear_pause(db: Session, request_type: str) -> None:
    state = db.get(LlmBudgetState, request_type)
    if state is not None:
        state.paused_until = None
        state.pause_reason = None
        db.flush()


def advisory_lock(db: Session, budget_day: date, request_type: str) -> None:
    """Transaction-scoped advisory lock keyed by (budget day, request type); released at commit or
    rollback. Taking it twice in one transaction is fine."""
    key = f"llm_budget:{budget_day.isoformat()}:{request_type}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
