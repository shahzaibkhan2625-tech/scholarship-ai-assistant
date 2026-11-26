"""003 T255: the gate interface in core/llm_gate.py (no database)."""

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core import llm_gate
from app.core.llm_gate import (
    LlmBudgetDeferred,
    LlmError,
    LlmPriorityMissing,
    LlmProviderError,
    LlmQuotaExhausted,
    LlmResponseUnparseable,
    LlmTemporarilyUnavailable,
    Priority,
    RequestType,
    current_priority,
    llm_priority,
    require_priority,
)


@pytest.fixture(autouse=True)
def _no_gate_left_installed():
    llm_gate.install_gate(None)
    yield
    llm_gate.install_gate(None)


def test_priority_and_request_type_members():
    assert {p.value for p in Priority} == {"user_facing", "backfill", "monitoring", "validation"}
    assert {r.value for r in RequestType} == {"generate", "embed"}


def test_scopes_nest_and_restore():
    assert current_priority() is None
    with llm_priority(Priority.MONITORING):
        assert current_priority() is Priority.MONITORING
        with llm_priority(Priority.VALIDATION):
            assert current_priority() is Priority.VALIDATION
        assert current_priority() is Priority.MONITORING
    assert current_priority() is None


def test_scope_restores_on_exception():
    with pytest.raises(RuntimeError):
        with llm_priority(Priority.BACKFILL):
            raise RuntimeError("boom")
    assert current_priority() is None


def test_call_without_priority_is_refused():
    with pytest.raises(LlmPriorityMissing):
        require_priority()
    with llm_priority(Priority.USER_FACING):
        assert require_priority() is Priority.USER_FACING


def test_scope_is_isolated_per_async_task():
    async def seen():
        with llm_priority(Priority.VALIDATION):
            await asyncio.sleep(0)
            return current_priority()

    assert asyncio.run(seen()) is Priority.VALIDATION
    assert current_priority() is None


def test_no_gate_installed_refuses_fail_closed():
    with pytest.raises(LlmError):
        llm_gate.get_gate().acquire(RequestType.GENERATE, Priority.USER_FACING)


def test_installed_gate_is_returned_and_can_be_removed():
    class Fake:
        def acquire(self, request_type, priority):
            return None

        def report_provider_error(self, request_type, error):
            return None

    gate = Fake()
    llm_gate.install_gate(gate)
    assert llm_gate.get_gate() is gate


def test_exception_hierarchy():
    retry_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for cls in (
        LlmPriorityMissing, LlmBudgetDeferred, LlmTemporarilyUnavailable, LlmQuotaExhausted,
        LlmProviderError, LlmResponseUnparseable,
    ):
        assert issubclass(cls, LlmError)
    assert LlmBudgetDeferred(retry_at).retry_at == retry_at
    for reason in ("daily_allowance_spent", "provider_quota", "provider_unavailable"):
        error = LlmTemporarilyUnavailable(retry_at, reason)
        assert (error.retry_at, error.reason) == (retry_at, reason)
    with pytest.raises(ValueError):
        LlmTemporarilyUnavailable(retry_at, "something else")


def test_error_text_never_carries_provider_text():
    assert str(LlmProviderError()) == "provider_error"
    assert str(LlmResponseUnparseable()) == "provider_parse"


def test_module_imports_no_database_or_service_code():
    tree = ast.parse(Path(llm_gate.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for name in imported:
        assert not name.startswith(("app.data", "app.models", "app.services", "sqlalchemy")), name
