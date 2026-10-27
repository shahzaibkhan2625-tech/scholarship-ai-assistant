"""Shared test fixtures. DB-touching tests need a Postgres at DATABASE_URL (CI uses a
localhost service container; locally use a throw-away one, see tests/README.md) and skip
themselves when it is absent or unreachable (mirrors tests/test_db_connection.py). Against
a non-local host they skip unless ALLOW_SHARED_DB=1 (tests/db_guard.py). Every fixture that
creates data cleans it up afterward.

`pytest --offline` keeps only the tests that need neither a database nor the network and
blocks every non-loopback socket and DNS lookup, so a leak fails loudly."""

import ipaddress
import logging
import re
import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from tests.db_guard import require_db_allowed


# --- --offline mode -------------------------------------------------------------------

# Fixtures through which a test reaches the database.
_DB_FIXTURES = {"db_session_factory", "client", "authed_user"}
# Modules that touch the database directly (SessionLocal / engine) or through fixtures local to
# the module; deselected whole, so a missed fixture cannot leak a connection.
_DIRECT_DB_MODULES = {
    "tests/api/test_monitoring_api.py",
    "tests/data/test_002_models.py",
    "tests/data/test_monitoring_repo.py",
    "tests/scheduling/test_locks.py",
    "tests/scheduling/test_scheduler.py",
    "tests/services/test_retention.py",
    "tests/sources/test_source_registry_compliance.py",
    "tests/test_auth.py",
    "tests/test_db_connection.py",
    "tests/workflows/test_source_monitor.py",
    "tests/workflows/test_source_monitor_rematch.py",
}

_offline_leaks: list[str] = []
_real_socket_ops: dict[str, object] = {}


def _is_loopback(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _blocked(kind: str, host) -> AssertionError:
    message = f"{kind} BLOCKED by --offline: {host}"
    _offline_leaks.append(message)
    return AssertionError(message)


def _install_network_block() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    _real_socket_ops.update(connect=real_connect, connect_ex=real_connect_ex, getaddrinfo=real_getaddrinfo)

    def check_address(address) -> None:
        host = address[0] if isinstance(address, tuple) else None  # str address = AF_UNIX path
        if not _is_loopback(host):
            raise _blocked("NETWORK ATTEMPT", host)

    def guarded_connect(self, address, *args, **kwargs):
        check_address(address)
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        check_address(address)
        return real_connect_ex(self, address, *args, **kwargs)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            raise _blocked("DNS LOOKUP", host)
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo


def pytest_addoption(parser):
    parser.addoption(
        "--offline",
        action="store_true",
        default=False,
        help="Run only tests that need neither the database nor the network; block non-loopback sockets and DNS.",
    )


def pytest_configure(config):
    if config.getoption("--offline"):
        _install_network_block()


def pytest_unconfigure(config):
    if _real_socket_ops:
        socket.socket.connect = _real_socket_ops.pop("connect")
        socket.socket.connect_ex = _real_socket_ops.pop("connect_ex")
        socket.getaddrinfo = _real_socket_ops.pop("getaddrinfo")


def _uses_db_fixture(item) -> bool:
    """True when a DB fixture defined in THIS file is in the item's fixture closure; a module that
    defines its own fixture of the same name (e.g. tests/core/test_request_id.py `client`) is not DB."""
    for name in _DB_FIXTURES & set(item.fixturenames):
        definitions = item._fixtureinfo.name2fixturedefs.get(name)
        if definitions and definitions[-1].func.__module__ == __name__:
            return True
    return False


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--offline"):
        return
    keep, drop = [], []
    for item in items:
        module = item.nodeid.split("::")[0]
        needs_db = _uses_db_fixture(item) or module in _DIRECT_DB_MODULES
        (drop if needs_db else keep).append(item)
    config.hook.pytest_deselected(items=drop)
    items[:] = keep


def pytest_sessionfinish(session, exitstatus):
    # A leak raises inside the code under test, which may swallow the error; fail the run regardless.
    if _offline_leaks and session.config.getoption("--offline"):
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter):
    if _offline_leaks:
        terminalreporter.section("--offline: blocked network access")
        for leak in _offline_leaks:
            terminalreporter.write_line(leak)


def find_registered_routes(app, method: str, path: str | re.Pattern) -> list:
    """Locate routes actually registered on `app`, matching against real
    sub-routes rather than `app.routes` entries directly.

    This FastAPI version (0.141.1) wraps each `include_router` call in an
    opaque `_IncludedRouter` on `app.routes` rather than exposing its
    sub-routes' full paths directly — a naive `route.path` scan over
    `app.routes` never matches anything routed through an included router
    (which is how every real route in this app is added), making such a scan
    a false positive/negative regardless of what's actually registered. The
    prefix from `include_context` must be joined with each sub-route's own
    path before comparing.

    `path` is either an exact string (equality match) or a compiled regex
    (`re.fullmatch`) for callers who don't know the future path template's
    parameter name (e.g. asserting a not-yet-implemented dynamic route)."""
    matches = []
    for route in app.routes:
        prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
        sub_routes = getattr(getattr(route, "original_router", None), "routes", None) or [route]
        for sub in sub_routes:
            if method not in (getattr(sub, "methods", None) or set()):
                continue
            full_path = f"{prefix}{getattr(sub, 'path', '') or ''}"
            if isinstance(path, re.Pattern):
                if path.fullmatch(full_path):
                    matches.append(sub)
            elif full_path == path:
                matches.append(sub)
    return matches


def _require_db():
    require_db_allowed()  # before any connection attempt
    try:
        from app.data.repositories.db import SessionLocal
    except RuntimeError:
        pytest.skip("DATABASE_URL is not configured; skipping DB-dependent tests.")

    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("Could not reach the configured database; skipping DB-dependent tests.")

    return SessionLocal


@pytest.fixture
def db_session_factory():
    """A session factory bound to a single connection wrapped in one outer
    transaction for the duration of the test, rolled back at teardown --
    SQLAlchemy's standard "join an external transaction" pattern for test
    isolation -- instead of relying on hand-written, FK-ordered deletes.

    `join_transaction_mode="create_savepoint"` means a `session.commit()`
    made by code under test (including FastAPI endpoints exercised through
    the `client` fixture below, via its `get_db` override) only releases a
    SAVEPOINT and immediately opens a new one; it can never end the outer
    transaction, so the rollback here always discards everything."""
    _require_db()  # skips the test if DATABASE_URL is unset/unreachable
    from app.data.repositories.db import engine

    connection = engine.connect()
    outer_transaction = connection.begin()
    TestingSessionLocal = sessionmaker(bind=connection, autoflush=False, join_transaction_mode="create_savepoint")

    yield TestingSessionLocal

    outer_transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session_factory):
    from app.data.repositories.db import get_db
    from app.main import app

    def _override_get_db():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _delete_user_cascade(session, user_id: uuid.UUID) -> None:
    from app.models.application import Application, SubmissionApproval, Task
    from app.models.document import ApplicationDocument, GeneratedDocument
    from app.models.match import Match
    from app.models.profile import EducationRecord, Experience, MissingInfo, Profile, ProfileCriterion, TestScore
    from app.models.user import User

    profile = session.query(Profile).filter(Profile.user_id == user_id).one_or_none()
    if profile is not None:
        session.query(ProfileCriterion).filter(ProfileCriterion.profile_id == profile.id).delete()
        session.query(MissingInfo).filter(MissingInfo.profile_id == profile.id).delete()
        session.query(EducationRecord).filter(EducationRecord.profile_id == profile.id).delete()
        session.query(TestScore).filter(TestScore.profile_id == profile.id).delete()
        session.query(Experience).filter(Experience.profile_id == profile.id).delete()
        session.delete(profile)

    # 002: alerts FK -> matches and users, so they go first.
    from app.models.alert import Alert, AlertPreferences

    session.query(Alert).filter(Alert.user_id == user_id).delete()
    session.query(AlertPreferences).filter(AlertPreferences.user_id == user_id).delete()
    session.query(Match).filter(Match.user_id == user_id).delete()

    # Phase 4 (T118/T120/T126): applications and everything FK-chained to
    # them must go before the user row, or the user delete below violates
    # applications_user_id_fkey (and application_documents/tasks' own FKs).
    application_ids = [
        row[0] for row in session.query(Application.id).filter(Application.user_id == user_id).all()
    ]
    if application_ids:
        session.query(SubmissionApproval).filter(SubmissionApproval.application_id.in_(application_ids)).delete(
            synchronize_session=False
        )
        session.query(Task).filter(Task.application_id.in_(application_ids)).delete(synchronize_session=False)
        session.query(GeneratedDocument).filter(GeneratedDocument.application_id.in_(application_ids)).delete(
            synchronize_session=False
        )
        session.query(ApplicationDocument).filter(ApplicationDocument.application_id.in_(application_ids)).delete(
            synchronize_session=False
        )
        session.query(Application).filter(Application.id.in_(application_ids)).delete(synchronize_session=False)

    user = session.get(User, user_id)
    if user is not None:
        session.delete(user)

    session.commit()


@pytest.fixture
def authed_user(client, db_session_factory):
    """Signs up + logs in a fresh throwaway user via the real auth endpoints,
    yields a dict with `client`, `token`, `user_id`, `headers`, and tears down
    every row the test created for that user afterward."""
    email = f"test-{uuid.uuid4().hex}@example.com"
    password = "s3cret-pass"  # noqa: S105 - throwaway test fixture credential, allow-secret

    signup = client.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201
    user_id = uuid.UUID(signup.json()["id"])

    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200
    token = login.json()["access_token"]

    yield {
        "client": client,
        "token": token,
        "user_id": user_id,
        "headers": {"Authorization": f"Bearer {token}"},
    }

    with db_session_factory() as session:
        _delete_user_cascade(session, user_id)


@pytest.fixture(autouse=True)
def _no_candidate_validation_in_monitor_runs(monkeypatch):
    """US7 T244: every monitoring run now validates pending candidate sources. The
    suite runs against the shared (real) Neon database, which may hold real pending
    candidates, so by default a run must see none: otherwise unrelated tests would
    write validation rows for, and run DNS/fetch/LLM against, real candidates.
    Tests that exercise the step (tests/workflows/test_source_monitor.py `cand_env`)
    re-patch this to a selection scoped to the candidates they created."""
    try:
        from app.data.repositories import candidate_validation_repo
    except RuntimeError:  # settings unavailable (no DATABASE_URL): nothing to guard
        return
    monkeypatch.setattr(candidate_validation_repo, "list_pending_needing_validation", lambda db, **kwargs: [])


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """US8: the app's limiter is process-global in-memory state and every `TestClient` request
    comes from the same address, so without a reset a long suite would eventually be
    throttled by tests that have nothing to do with rate limiting."""
    import sys

    def _reset() -> None:
        main = sys.modules.get("app.main")
        if main is not None:
            main.rate_limiter.reset()

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _restore_logging_config():
    """US8: `configure_logging()` mutates process-global state (root handler + level,
    uvicorn loggers). Tests that run the real app lifespan (`with TestClient(app)`)
    trigger it, and without this it would leak into every later test. Only handlers
    WE installed are removed: pytest's own capture handlers are attached/detached per
    phase, and restoring a snapshot of `root.handlers` would resurrect stale ones."""
    root = logging.getLogger()
    root_level = root.level
    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate, logging.getLogger(name).level)
        for name in names
    }
    yield
    for handler in [h for h in root.handlers if getattr(h, "_scholarship_json_handler", False)]:
        root.removeHandler(handler)
    root.setLevel(root_level)
    for name, (handlers, propagate, level) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.setLevel(level)
