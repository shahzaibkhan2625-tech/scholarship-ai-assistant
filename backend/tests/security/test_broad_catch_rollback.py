"""010 US7 T415/T423 (FR-RECOV-1, SC-007): a broad `except Exception` that can swallow a database
error must roll the session back, or the next write on that session fails with "transaction is
aborted". This guard walks every module under `app/`: each handler for `Exception`, `BaseException`
or a bare `except` must contain a `rollback`/`safe_rollback` call, re-raise, or be listed below
with the reason it cannot leave a session in a failed state. Offline: reads source only."""

import ast
import logging
from pathlib import Path
from unittest.mock import MagicMock

APP_DIR = Path(__file__).resolve().parents[2] / "app"
BROAD = {"Exception", "BaseException"}
ROLLBACK_CALLS = {"rollback", "safe_rollback"}

# "app/<path>:<enclosing function>" -> why no rollback is needed. Keep each reason specific: a new
# entry is a claim that the `try` body never leaves a Session in a failed transaction.
ALLOW_LIST = {
    "app/api/monitoring.py:_execute_run": "own `with SessionLocal()` block; the session is closed on exit and never reused",
    "app/api/profile.py:_extract_text_from_upload": "import_cv PDF parsing only (pypdf); touches no database",
    "app/core/db_role_check.py:check_db_role": "one read-only SELECT on its own start-up connection, closed on exit; returns without writing",
    "app/data/repositories/db.py:safe_rollback": "the handler for a failing rollback itself; it issues no further statement on the session",
    "app/main.py:lifespan": "start-up/shutdown hooks; each runs on its own connection or session that is closed on exit",
    "app/scheduling/scheduler.py:_run_scheduled_monitor": "own `with SessionLocal()` block; the session is closed on exit and never reused",
    "app/scheduling/scheduler.py:_run_scheduled_retention": "own `with SessionLocal()` block; the session is closed on exit and never reused",
    "app/services/alerts.py:send": "SMTP only; the email channel touches no database",
    "app/services/alerts.py:dispatch_alert": "channel.send only mutates the in-memory Alert; alert_repo.save runs outside the try",
    "app/services/candidate_validation.py:_reachability": "HTTP fetch only; no database access in the try body",
    "app/services/candidate_validation.py:_extractability": "LLM extraction only; no database access in the try body",
    "app/sources/retry_policy.py:run_with_retry": "generic retry wrapper; the failing callable's owner rolls back (see ingestion _run_stage)",
    "app/tools/classify.py:classify": "LLM call only; no database access",
    "app/workflows/ingestion/graph.py:_index_node": "vector-store indexing only (rag/embed.py); no Session in the try body",
}


def _enclosing_function(node, parents):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return "<module>"


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(n, ast.Name) and n.id in BROAD for n in names)


def _handles_safely(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in ROLLBACK_CALLS:
                return True
    return False


def _broad_handlers():
    found = []  # (key, path:line, safe)
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        relative = f"app/{path.relative_to(APP_DIR).as_posix()}"
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_broad(node):
                key = f"{relative}:{_enclosing_function(node, parents)}"
                found.append((key, f"{relative}:{node.lineno}", _handles_safely(node)))
    return found


def test_every_broad_catch_rolls_back_re_raises_or_is_allow_listed():
    offenders = [where for key, where, safe in _broad_handlers() if not safe and key not in ALLOW_LIST]
    assert offenders == [], (
        "broad `except` handlers that neither roll back nor re-raise and are not in ALLOW_LIST "
        f"(call safe_rollback(db) or add a reasoned entry): {offenders}"
    )


def test_allow_list_has_no_stale_entries():
    keys = {key for key, _, _ in _broad_handlers()}
    assert sorted(set(ALLOW_LIST) - keys) == []


def test_every_allow_list_entry_has_a_reason():
    assert all(len(reason) >= 20 for reason in ALLOW_LIST.values())


def test_the_guard_flags_a_broad_catch_without_rollback():
    tree = ast.parse("def f(db):\n    try:\n        db.commit()\n    except Exception:\n        pass\n")
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler))
    assert _is_broad(handler) and not _handles_safely(handler)
    fixed = ast.parse("def f(db):\n    try:\n        db.commit()\n    except Exception:\n        safe_rollback(db)\n")
    assert _handles_safely(next(n for n in ast.walk(fixed) if isinstance(n, ast.ExceptHandler)))


def test_safe_rollback_rolls_back_and_a_failing_rollback_cannot_mask_the_original_error(caplog):
    from app.data.repositories.db import safe_rollback

    db = MagicMock()
    safe_rollback(db)
    db.rollback.assert_called_once_with()

    db.rollback.side_effect = RuntimeError("connection is gone")
    with caplog.at_level(logging.WARNING):
        safe_rollback(db)  # must not raise
    assert "RuntimeError" in caplog.text and "connection is gone" not in caplog.text
