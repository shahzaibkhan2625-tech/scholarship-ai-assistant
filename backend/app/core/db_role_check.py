"""Start-up check that the app connects with a least-privilege database role (010 US3, FR-DBROLE-5).

Logs one WARNING naming which owner-level flags are true (never the URL or password) and
never raises, so a misconfigured or unreachable database cannot stop the API from booting.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

FLAGS = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls", "can_create_in_public")

_QUERY = text(
    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, "
    "has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_in_public "
    "FROM pg_roles WHERE rolname = current_user"
)


def check_db_role(connection) -> list[str]:
    """Return the owner-level flags that are true for the connected role (and log them once)."""
    try:
        row = connection.execute(_QUERY).one()
        flagged = [flag for flag, value in zip(FLAGS, row) if value]
    except Exception as exc:  # noqa: BLE001 - must never block start-up
        logger.warning("database role check skipped: %s", type(exc).__name__)
        return []
    if flagged:
        logger.warning("database credentials have owner-level rights: %s", ", ".join(flagged))
    return flagged


def check_current_db_role() -> list[str]:
    """Run the check on a fresh connection from the application engine."""
    from app.data.repositories.db import engine

    with engine.connect() as connection:
        return check_db_role(connection)
