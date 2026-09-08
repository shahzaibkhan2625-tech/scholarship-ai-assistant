"""Discovery Agent guardrail tests (T089, PRD B3 Hard-Gated Zone: "No
self-authorized sources... Connectors reject any fetch whose source_id is
not active — LLM cannot override this"). Verified by static AST inspection
of the actual module source, not by convention/docstring — mirrors
tests/agents/test_matching_guardrails.py's `test_matching_agent_module_has_
no_web_or_fetch_tool_access` pattern.
"""

import ast
import inspect

from app.agents.discovery import agent as discovery_agent_module

# Any of these being called on `source_repo` from inside the agent module
# would let it self-authorize a source (write to source_registry/
# candidate_sources) or silently mark one failing outside the connector's
# own retry/log accounting — both are hard-gated in PRD B3.
_FORBIDDEN_SOURCE_REPO_WRITE_METHODS = {
    "upsert_source_by_domain",
    "create_candidate_source",
    "approve_candidate_source",
    "reject_candidate_source",
    "mark_source_failing",
    "log_fetch",
}

# Raw transport / raw registry-model access would let the agent bypass the
# active-only gate that `official_fetch_tool`/`api_connector_tool` (built on
# `source_repo.get_active_sources`/`get_source_by_id`) already enforce.
_FORBIDDEN_IMPORT_FRAGMENTS = ["httpx", "requests", "tools.web_fetch"]


def _module_source_and_tree():
    source = inspect.getsource(discovery_agent_module)
    return source, ast.parse(source)


def test_agent_never_calls_a_source_repo_write_or_log_method():
    """AST-level proof: no `source_repo.<write-method>(...)` call site exists
    anywhere in the module, however deeply nested (guardrail (a): no
    self-authorized sources)."""
    _, tree = _module_source_and_tree()

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "source_repo":
            if node.attr in _FORBIDDEN_SOURCE_REPO_WRITE_METHODS:
                offending.append(node.attr)

    assert not offending, (
        f"Discovery agent must never call source_repo write/log methods directly "
        f"(PRD B3 no-self-authorization): found {offending}"
    )


def test_agent_only_reads_sources_via_the_active_only_gate():
    """The only `source_repo` functions the agent may call are the
    active-only-gated reads (`get_active_sources`, `get_source_by_id`) — the
    same functions the governed tools themselves are built on."""
    _, tree = _module_source_and_tree()

    allowed = {"get_active_sources", "get_source_by_id"}
    called_source_repo_methods = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "source_repo"
    }

    assert called_source_repo_methods, "expected the agent to call source_repo at all (sanity check on this test)"
    assert called_source_repo_methods <= allowed, (
        f"Discovery agent called unexpected source_repo methods {called_source_repo_methods - allowed}; "
        f"only the active-only-gated reads {allowed} are permitted."
    )


def test_agent_module_has_no_raw_transport_or_raw_fetch_tool_access():
    """No httpx/requests/raw web_fetch import — every network call must go
    through the already-gated `official_fetch_tool`/`api_connector_tool`."""
    source, tree = _module_source_and_tree()

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
            imported_modules.update(f"{node.module}.{alias.name}" for alias in node.names)

    for fragment in _FORBIDDEN_IMPORT_FRAGMENTS:
        offending = [m for m in imported_modules if fragment in m]
        assert not offending, f"Discovery agent must not import {fragment!r}: found {offending}"


def test_agent_never_constructs_a_sourceregistry_row_directly():
    """No direct `SourceRegistry(...)` instantiation anywhere in the module —
    a new authoritative row may only ever come from the governed
    `source_validate` workflow (T086), never from this agent."""
    _, tree = _module_source_and_tree()

    offending = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "SourceRegistry"
    ]
    assert not offending, "Discovery agent must never construct a SourceRegistry row directly."
