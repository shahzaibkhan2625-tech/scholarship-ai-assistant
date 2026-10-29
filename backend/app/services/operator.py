"""Operator management CLI (010 FR-AUTHZ-3/4, FR-AUDIT-1; contracts/operator-cli.md).

Operator status lives on `users.is_operator` and is changed ONLY here: no HTTP route can grant
or revoke it. The role is read from the row on every request, so a change applies to the user's
next request with no token reissue.

    python -m app.services.operator grant  --email EMAIL
    python -m app.services.operator revoke --email EMAIL

Exit codes: 0 ok / no-op, 1 no such user, 2 bad arguments (argparse). Output never prints
password hashes or tokens.
"""

import argparse
import logging
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.repositories import audit_repo
from app.models.user import User

logger = logging.getLogger(__name__)

__all__ = ["main"]


def _find_user(db: Session, email: str) -> User | None:
    return db.scalars(select(User).where(func.lower(User.email) == email)).one_or_none()


def _grant(db: Session, user: User) -> str:
    if user.is_operator:
        return f"{user.email} is already an operator"
    user.is_operator = True
    audit_repo.record(db, user.id, "operator.grant", f"user:{user.id}")
    db.commit()  # the flag and its audit row land together
    logger.info("operator granted", extra={"user_id": str(user.id)})
    return f"granted operator status to {user.email}"


def _revoke(db: Session, user: User) -> str:
    if not user.is_operator:
        return f"{user.email} is not an operator"
    user.is_operator = False
    db.commit()
    logger.info("operator revoked", extra={"user_id": str(user.id)})
    return f"revoked operator status from {user.email}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Grant or revoke operator status for one user.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("grant", "make a user an operator"), ("revoke", "remove operator status")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--email", required=True, help="the user's email (case-insensitive)")
    args = parser.parse_args(argv)
    email = args.email.strip().lower()

    from app.data.repositories.db import SessionLocal

    with SessionLocal() as db:
        user = _find_user(db, email)
        if user is None:
            print(f"no such user: {email}")
            return 1
        print(_grant(db, user) if args.command == "grant" else _revoke(db, user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
