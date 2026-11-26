"""Architecture guardrails for the `source_monitor` workflow (T171, FR-MON-5).

Monitoring is deterministic: no LLM, no agents. These AST checks keep that true. They were
missing from the US1 commit (found in the B4 tick-sync) although the invariant held; this
file closes the gap.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
WORKFLOW_DIR = APP / "workflows" / "source_monitor"
AGENTS_DIR = APP / "agents"

FORBIDDEN_PREFIXES = ("app.agents", "app.core.llm", "langchain", "google.genai", "google.generativeai")
# 003 T274 (spec-directed amendment): the monitor declares its priority class with `llm_priority`. That
# interface module holds enums, a context variable and exceptions only (no provider access, AST-checked in
# tests/core/test_llm_gate.py), so it is the one `app.core.llm*` module the monitor may import. The provider
# wrapper `app.core.llm` itself, agents, langchain and genai stay forbidden.
ALLOWED_INTERFACE_MODULES = ("app.core.llm_gate",)


def forbidden_imports(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # `from app import agents` / `from app.core import llm` spell the module in the names
            names = [module] + [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        found += [
            n for n in names if n.startswith(FORBIDDEN_PREFIXES) and not n.startswith(ALLOWED_INTERFACE_MODULES)
        ]
    return found


def test_workflow_package_has_python_files_to_check():
    assert len(list(WORKFLOW_DIR.glob("*.py"))) >= 2


def test_source_monitor_imports_no_agent_or_llm_module():
    offenders = {
        path.name: forbidden_imports(path.read_text(encoding="utf-8"))
        for path in WORKFLOW_DIR.glob("*.py")
        if forbidden_imports(path.read_text(encoding="utf-8"))
    }
    assert offenders == {}


def test_no_agent_module_or_registry_mentions_source_monitor():
    offenders = [
        str(path.relative_to(APP))
        for path in AGENTS_DIR.rglob("*.py")
        if "source_monitor" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_checker_detects_planted_violations():
    assert forbidden_imports("from app.agents.discovery import agent")
    assert forbidden_imports("import langchain_core")
    assert forbidden_imports("from google import genai\nfrom google.genai import types")
    assert forbidden_imports("from app.core import llm") == ["app.core.llm"]
    assert forbidden_imports("from app.core.llm import generate_text")
    assert forbidden_imports("from app.core.llm_errors import classify_provider_error")
    assert forbidden_imports("from app.core.llm_gate import Priority, llm_priority") == []
    assert forbidden_imports("from app.data.repositories import source_repo") == []
