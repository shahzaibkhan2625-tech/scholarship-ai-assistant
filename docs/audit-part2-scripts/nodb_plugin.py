# Audit Part 2 helper (read-only, not application code): pytest plugin that keeps only the tests needing neither the DB nor the network.
# Run from backend:  DATABASE_URL=postgresql+psycopg://nobody:nopass@127.0.0.1:1/none GEMINI_API_KEY=not-a-real-key QDRANT_URL=http://127.0.0.1:1 QDRANT_API_KEY=x \
#   PYTHONPATH=../docs/audit-part2-scripts python -m pytest -q -p no:cacheprovider -p nodb_plugin -m "not smoke"
"""pytest plugin: keep only tests that need neither the database nor the network,
and hard-block sockets so any leak fails loudly instead of connecting."""
import socket

DB_FIXTURES = {"db_session_factory", "client", "authed_user"}
DIRECT_DB_MODULES = {
    "tests/api/test_monitoring_api.py", "tests/data/test_002_models.py", "tests/data/test_monitoring_repo.py",
    "tests/scheduling/test_locks.py", "tests/scheduling/test_scheduler.py", "tests/services/test_retention.py",
    "tests/sources/test_source_registry_compliance.py", "tests/test_auth.py", "tests/test_db_connection.py",
    "tests/workflows/test_source_monitor.py", "tests/workflows/test_source_monitor_rematch.py",
}


_real_connect = socket.socket.connect
_real_gai = socket.getaddrinfo
_LOOP = {"127.0.0.1", "::1", "localhost"}


def _guard_connect(self, address, *a, **k):
    host = address[0] if isinstance(address, tuple) else address
    if host not in _LOOP:
        raise AssertionError(f"NETWORK ATTEMPT BLOCKED by audit plugin: {host}")
    return _real_connect(self, address, *a, **k)


def _guard_gai(host, *a, **k):
    if host not in _LOOP and host is not None:
        raise AssertionError(f"DNS LOOKUP BLOCKED by audit plugin: {host}")
    return _real_gai(host, *a, **k)


socket.socket.connect = _guard_connect
socket.getaddrinfo = _guard_gai


def pytest_collection_modifyitems(config, items):
    keep, drop = [], []
    for it in items:
        mod = it.nodeid.split("::")[0]
        needs_db = bool(set(it.fixturenames) & DB_FIXTURES) or mod in DIRECT_DB_MODULES
        (drop if needs_db else keep).append(it)
    config.hook.pytest_deselected(items=drop)
    items[:] = keep
