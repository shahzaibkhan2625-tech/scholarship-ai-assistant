"""003 T254: static checks of the quota-governance revision file (no DB connection)."""

import ast
import importlib.util
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "migrations" / "versions"
PRIOR_HEAD = "a807daf8460e"  # 010's security-hardening revision (confirmed with `alembic heads`)
TABLES = ["classify_pending", "llm_budget_ledger", "llm_budget_state", "source_check_state"]
COLUMNS = [
    ("monitoring_runs", "sources_deferred"),
    ("monitoring_runs", "sources_skipped"),
    ("source_fetch_log", "outcome"),
]


def _path() -> Path:
    paths = list(VERSIONS.glob("*_quota_governance.py"))
    assert len(paths) == 1, "expected exactly one '*_quota_governance.py' revision"
    return paths[0]


def _function_source(name: str) -> str:
    source = _path().read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found")


def test_revision_chains_from_the_010_head():
    spec = importlib.util.spec_from_file_location("quota_revision", _path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.down_revision == PRIOR_HEAD


def test_header_states_the_apply_order():
    text = _path().read_text(encoding="utf-8").lower()
    assert "additive only" in text and "owner" in text and "role script" in text


def test_upgrade_only_creates_four_tables_and_three_columns():
    up = _function_source("upgrade")
    assert sorted(re.findall(r"create_table\(\s*['\"](\w+)['\"]", up)) == TABLES
    assert sorted(re.findall(r"add_column\(\s*['\"](\w+)['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"]", up)) == COLUMNS
    for forbidden in ["drop_", "alter_column", "CheckConstraint", "GRANT", "execute("]:
        assert forbidden not in up, forbidden


def test_downgrade_removes_exactly_those_objects():
    down = _function_source("downgrade")
    assert sorted(re.findall(r"drop_table\(\s*['\"](\w+)['\"]", down)) == TABLES
    assert sorted(re.findall(r"drop_column\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]", down)) == COLUMNS


def test_single_head():
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
