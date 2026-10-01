"""Connector-tier (`official_fetch.py`) guardrail tests (US4 Acceptance
Scenario 3, ADR-0005). Mirrors `tests/agents/test_discovery_guardrails.py`'s
AST-based pattern: this module gained `source_repo` write access via
`record_or_bump_candidate_source` (Slice 2/3 of this feature), so it needs
the same static proof the Discovery Agent module already has — nothing here
can promote a candidate to active or fabricate a `SourceRegistry` row
directly. Promotion remains solely reachable via `source_validate`'s
human-approval gate (T086).
"""

import ast
import inspect

from app.sources.connectors import official_fetch as official_fetch_module

# Approving/rejecting a candidate, or directly creating one (bypassing the
# dedup/bump logic in `record_or_bump_candidate_source`), or re-seeding a
# source by domain, would all let this connector self-authorize or otherwise
# short-circuit the governed candidate lifecycle (constitution Principle IV).
_FORBIDDEN_SOURCE_REPO_METHODS = {
    "approve_candidate_source",
    "reject_candidate_source",
    "create_candidate_source",
    "upsert_source_by_domain",
}


def _module_source_and_tree():
    source = inspect.getsource(official_fetch_module)
    return source, ast.parse(source)


def test_connector_never_calls_approval_reject_or_direct_create_methods():
    """AST-level proof: no `source_repo.<forbidden>(...)` call site exists
    anywhere in the module, however deeply nested."""
    _, tree = _module_source_and_tree()

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "source_repo":
            if node.attr in _FORBIDDEN_SOURCE_REPO_METHODS:
                offending.append(node.attr)

    assert not offending, (
        f"official_fetch.py must never call source_repo approval/reject/direct-create "
        f"methods (ADR-0005: promotion only via source_validate's human gate): found {offending}"
    )


def test_connector_candidate_write_path_is_record_or_bump_only():
    """The only candidate-related `source_repo` call this module may make is
    `record_or_bump_candidate_source` — confirms no other candidate write
    method was introduced alongside it."""
    _, tree = _module_source_and_tree()

    called = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "source_repo"
    }
    candidate_related = {method for method in called if "candidate" in method.lower()}

    assert candidate_related == {"record_or_bump_candidate_source"}, (
        f"Unexpected candidate-related source_repo calls in official_fetch.py: {candidate_related}"
    )


def test_connector_never_constructs_a_sourceregistry_row_directly():
    """No direct `SourceRegistry(...)` instantiation anywhere in the module —
    a new authoritative row may only ever come from the governed
    `source_validate` workflow (T086), never from this connector."""
    _, tree = _module_source_and_tree()

    offending = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "SourceRegistry"
    ]
    assert not offending, "official_fetch.py must never construct a SourceRegistry row directly."
