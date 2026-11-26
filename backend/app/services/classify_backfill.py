"""Drain of the pending-classification queue (003 FR-BUDGET-13, FR-BUDGET-10).

A record whose classify LLM call was refused or failed is stored without the dimensions the rules could not
resolve and queued (`classify_pending`). Each scheduler tick calls `drain_classify_pending`, which runs under
the `backfill` class (the gate keeps the user-facing reserve intact), takes the oldest rows first, runs the
same `classify` tool over the record's text and fills ONLY dimensions that are still unknown: a value a person
or a monitoring run set in the meantime is never overwritten. The row is deleted once classify produced an
answer; a failure counts an attempt and keeps the row; a refusal by the gate leaves the row untouched and ends
the drain (the rest would be refused too). Every attempt is logged by id and outcome, never with record text.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.llm_gate import Priority, llm_priority
from app.data.repositories import classify_pending_repo
from app.models.scholarship import DegreeLevel, FundingStatus, Scholarship, ScholarshipField
from app.tools.classify import ClassificationResult
from app.tools.classify import classify as classify_tool

logger = logging.getLogger(__name__)

__all__ = ["DRAIN_BATCH", "DrainResult", "drain_classify_pending"]

# Rows looked at per tick; the gate's backfill share is the real bound on provider requests.
DRAIN_BATCH = 10

# A refusal, a spent quota or an outage will refuse the rest too; an unparseable reply is per record.
_STOP_REASONS = frozenset({"budget_deferred", "provider_quota", "provider_error", "not_configured"})


@dataclass(frozen=True)
class DrainResult:
    answered: int = 0  # rows deleted: classify produced an answer
    failed: int = 0  # rows that counted a failed attempt
    refused: int = 0  # rows left untouched because the gate refused


def _record_text(scholarship: Scholarship) -> str:
    parts = (
        scholarship.name, scholarship.provider, scholarship.country, scholarship.field, scholarship.intake,
        scholarship.application_procedure, scholarship.conditions, scholarship.exceptions, scholarship.notes,
    )
    return " ".join(str(part) for part in parts if part)


def _coerce(enum_cls, value):
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


def _fill_unknown(scholarship: Scholarship, result: ClassificationResult) -> None:
    """Write only what is still unknown; confidence is `inferred` (classify never emits `verified`)."""
    if scholarship.degree_level is None:
        scholarship.degree_level = _coerce(DegreeLevel, result.degree_level.value)
    if scholarship.funding_status == FundingStatus.UNKNOWN:
        scholarship.funding_status = _coerce(FundingStatus, result.funding_type.value) or FundingStatus.UNKNOWN
    if not scholarship.country and result.country.value:
        scholarship.country = result.country.value
    if not scholarship.field and result.field.value:
        scholarship.field = result.field.value
    has_provider_type = any(f.key == "provider_type" for f in scholarship.fields)
    if not has_provider_type and result.provider_type.value:
        scholarship.fields.append(
            ScholarshipField(
                key="provider_type",
                value=result.provider_type.value,
                value_status="known",
                confidence=result.provider_type.confidence,
            )
        )


def drain_classify_pending(db: Session, *, now: datetime | None = None, limit: int = DRAIN_BATCH) -> DrainResult:
    now = now or datetime.now(timezone.utc)
    entries = [entry.scholarship_id for entry in classify_pending_repo.list_oldest(db, limit)]
    db.commit()  # end the read transaction before any provider call
    answered = failed = refused = 0
    with llm_priority(Priority.BACKFILL):
        for scholarship_id in entries:
            scholarship = db.get(Scholarship, scholarship_id)
            if scholarship is None:  # deleted since: the cascade already removed the row
                continue
            result = classify_tool(_record_text(scholarship))
            reason = result.deferred_reason
            if reason == "budget_deferred":
                refused += 1
                logger.info("classify drain attempt", extra={"scholarship_id": str(scholarship_id), "outcome": reason})
                break
            if reason is not None:
                classify_pending_repo.record_attempt(db, scholarship_id, reason, now=now)
                db.commit()
                failed += 1
                logger.info("classify drain attempt", extra={"scholarship_id": str(scholarship_id), "outcome": reason})
                if reason in _STOP_REASONS:
                    break
                continue
            _fill_unknown(scholarship, result)
            classify_pending_repo.delete(db, scholarship_id)
            db.commit()
            answered += 1
            logger.info("classify drain attempt", extra={"scholarship_id": str(scholarship_id), "outcome": "answered"})
    return DrainResult(answered=answered, failed=failed, refused=refused)
