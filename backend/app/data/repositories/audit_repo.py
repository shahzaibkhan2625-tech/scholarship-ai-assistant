"""Audit trail writes (010 FR-AUDIT-1/2).

`record` only adds the row to the caller's session: it never commits or flushes, so the audit
row lands in the SAME transaction as the action it describes (written only on success) and a
rollback removes both."""

import uuid

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

AUDIT_ACTIONS = frozenset(
    {
        "user.signup",
        "source.approve",
        "source.reject",
        "monitoring.run.manual",
        "submission.approve",
        "operator.grant",
    }
)


def record(db: Session, user_id: uuid.UUID, action: str, entity: str) -> AuditLog:
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    row = AuditLog(user_id=user_id, action=action, entity=entity)
    db.add(row)
    return row
