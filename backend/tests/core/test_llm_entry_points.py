"""003 T270: guard. Every module under app/ that calls `generate_text` or `embed_texts` must be listed
below with the entry-point class(es) it runs under, so a new LLM-backed path cannot be added
unclassified (FR-BUDGET-1/3). Update the map deliberately when a path is added or removed."""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
_CALLEES = {"generate_text", "embed_texts"}

U, M, V, B = "user_facing", "monitoring", "validation", "backfill"

# module (relative to app/) -> classes it runs under
ALLOWED_CALLERS = {
    "agents/matching/agent.py": {U, M},  # student match; alert re-match pair under monitoring
    "agents/research_qa/agent.py": {U},
    "tools/classify.py": {U, M, V, B},  # ingestion, candidate validation, pending-classification drain
    "tools/embed.py": {U, M, B},
    "tools/extract_document_fields.py": {U},
    "tools/extract_listing.py": {U, M, V},
    "tools/extract_profile.py": {U},
    "tools/extract_requirements.py": {U, M},
    "workflows/cv_gen/graph.py": {U},
    "workflows/sop_gen/graph.py": {U},
}
# the provider wrapper itself defines both functions
DEFINES = {"core/llm.py"}


def _callers() -> set[str]:
    found = set()
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                if name in _CALLEES:
                    found.add(relative)
    return found - DEFINES


def test_every_llm_calling_module_is_classified():
    unclassified = _callers() - set(ALLOWED_CALLERS)
    assert not unclassified, f"LLM-backed module(s) without an entry-point class: {sorted(unclassified)}"


def test_the_map_has_no_stale_entries():
    stale = set(ALLOWED_CALLERS) - _callers()
    assert not stale, f"classified module(s) that no longer call the provider: {sorted(stale)}"


def test_every_entry_names_a_known_class():
    for module, classes in ALLOWED_CALLERS.items():
        assert classes and classes <= {U, M, V, B}, module
