"""LLM budget counters (003, data-model.md): the daily ledger and the provider-pause state.

System-owned, no `user_id`. Every FR-OBS-1 number is a sum over the ledger, so it survives restarts
and is shared by all workers (FR-BUDGET-8).
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.repositories.db import Base


class LlmBudgetLedger(Base):
    __tablename__ = "llm_budget_ledger"

    budget_day: Mapped[date] = mapped_column(Date, primary_key=True)
    request_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    priority_class: Mapped[str] = mapped_column(String(24), primary_key=True)
    event: Mapped[str] = mapped_column(String(24), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LlmBudgetState(Base):
    __tablename__ = "llm_budget_state"

    request_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pause_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
