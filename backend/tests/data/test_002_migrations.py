"""T146: static (no DB, no alembic invocation) checks on the two 002
migration revisions. Revision files are parsed with `ast`, never imported
or executed."""

import ast
import re
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
PRIOR_HEAD = "ad4826bd57e6"
FORBIDDEN_OPS = {"drop_table", "drop_column", "alter_column", "drop_constraint", "execute"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _assigned_str(tree: ast.Module, name: str) -> str | None:
    for node in tree.body:
        target, value = None, None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target == name and isinstance(value, ast.Constant):
            return value.value
    return None


def _all_revisions() -> dict[str, tuple[Path, str | None]]:
    out = {}
    for path in VERSIONS.glob("*.py"):
        tree = _parse(path)
        rev = _assigned_str(tree, "revision")
        if rev:
            out[rev] = (path, _assigned_str(tree, "down_revision"))
    return out


def _children_of(parent: str) -> list[Path]:
    return [p for rev, (p, down) in _all_revisions().items() if down == parent]


def _rev_a() -> Path:
    candidates = [p for p in _children_of(PRIOR_HEAD) if "add_monitoring_columns_and_runs" in p.name]
    assert len(candidates) == 1, "expected exactly one '*_add_monitoring_columns_and_runs.py' on top of ad4826bd57e6"
    return candidates[0]


def _rev_b() -> Path:
    a_rev = _assigned_str(_parse(_rev_a()), "revision")
    candidates = [p for p in _children_of(a_rev) if "add_alerts_and_candidate_validations" in p.name]
    assert len(candidates) == 1, "expected exactly one '*_add_alerts_and_candidate_validations.py' on top of revision (a)"
    return candidates[0]


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"function {name!r} not found")


def _op_calls(fn: ast.FunctionDef) -> list[tuple[str, ast.Call]]:
    calls = []
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.func.attr != "f"  # op.f(...) only wraps an index/constraint name
        ):
            calls.append((node.func.attr, node))
    return sorted(calls, key=lambda c: (c[1].lineno, c[1].col_offset))


def _first_str_arg(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant):
        return call.args[0].value
    return None


def _added_column_name(call: ast.Call) -> str:
    # op.add_column('table', sa.Column('name', ...))
    return call.args[1].args[0].value


def test_both_revision_files_exist():
    assert _rev_a().exists()
    assert _rev_b().exists()


def test_revision_a_chains_from_current_head():
    assert _assigned_str(_parse(_rev_a()), "down_revision") == PRIOR_HEAD


def test_revision_b_chains_from_revision_a():
    a_rev = _assigned_str(_parse(_rev_a()), "revision")
    assert _assigned_str(_parse(_rev_b()), "down_revision") == a_rev


def test_exactly_one_head():
    revs = _all_revisions()
    parents = {down for _, down in revs.values() if down}
    heads = [rev for rev in revs if rev not in parents]
    assert len(heads) == 1, f"expected a single head, got {heads}"
    assert heads[0] == _assigned_str(_parse(_rev_b()), "revision")


@pytest.mark.parametrize("which", [_rev_a, _rev_b])
def test_upgrade_is_additive_only(which):
    upgrade = _func(_parse(which()), "upgrade")
    ops = [name for name, _ in _op_calls(upgrade)]
    assert ops, "upgrade() performs no operations"
    assert not (set(ops) & FORBIDDEN_OPS), f"non-additive ops in upgrade(): {set(ops) & FORBIDDEN_OPS}"


def _key(name: str, call: ast.Call):
    if name in ("create_table", "drop_table"):
        return ("table", _first_str_arg(call))
    if name == "add_column":
        return ("column", _first_str_arg(call), _added_column_name(call))
    if name == "drop_column":
        return ("column", _first_str_arg(call), call.args[1].value)
    if name in ("create_index", "drop_index"):
        first = call.args[0]
        # op.f('ix_name') is a Call; a plain string is a Constant
        return ("index", first.args[0].value if isinstance(first, ast.Call) else first.value)
    raise AssertionError(f"unexpected op {name}")


@pytest.mark.parametrize("which", [_rev_a, _rev_b])
def test_every_upgrade_has_a_matching_downgrade(which):
    tree = _parse(which())
    up = _op_calls(_func(tree, "upgrade"))
    down = _op_calls(_func(tree, "downgrade"))
    inverse = {"create_table": "drop_table", "add_column": "drop_column", "create_index": "drop_index"}

    for name, call in up:
        assert name in inverse, f"upgrade uses op.{name} with no known inverse"
        wanted = (inverse[name], _key(name, call))
        assert any((n, _key(n, c)) == wanted for n, c in down), (
            f"downgrade missing inverse of {name} {_key(name, call)}"
        )
    assert len(down) == len(up)


def test_downgrade_drops_tables_in_reverse_creation_order():
    for which in (_rev_a, _rev_b):
        tree = _parse(which())
        up = [_first_str_arg(c) for n, c in _op_calls(_func(tree, "upgrade")) if n == "create_table"]
        down = [_first_str_arg(c) for n, c in _op_calls(_func(tree, "downgrade")) if n == "drop_table"]
        assert down == list(reversed(up))


def test_monitoring_runs_created_before_fk_column_that_references_it():
    up = _op_calls(_func(_parse(_rev_a()), "upgrade"))
    create_idx = next(i for i, (n, c) in enumerate(up) if n == "create_table" and _first_str_arg(c) == "monitoring_runs")
    fk_idx = next(
        i
        for i, (n, c) in enumerate(up)
        if n == "add_column" and _first_str_arg(c) == "source_fetch_log" and _added_column_name(c) == "monitoring_run_id"
    )
    assert create_idx < fk_idx


def test_revision_a_contents():
    up = _op_calls(_func(_parse(_rev_a()), "upgrade"))
    added = {(_first_str_arg(c), _added_column_name(c)) for n, c in up if n == "add_column"}
    assert added == {
        ("source_registry", "listing_page_url"),
        ("source_registry", "freshness_window_days"),
        ("source_fetch_log", "fetched_url"),
        ("source_fetch_log", "used_homepage_fallback"),
        ("source_fetch_log", "monitoring_run_id"),
    }
    created = {_first_str_arg(c) for n, c in up if n == "create_table"}
    assert created == {"monitoring_runs"}


def test_revision_a_used_homepage_fallback_has_server_default():
    src = _rev_a().read_text(encoding="utf-8")
    assert re.search(r"used_homepage_fallback.*?server_default=", src, re.S)


def test_revision_b_contents():
    up = _op_calls(_func(_parse(_rev_b()), "upgrade"))
    created = {_first_str_arg(c) for n, c in up if n == "create_table"}
    assert created == {"alerts", "alert_preferences", "candidate_source_validations"}
    assert "read_at" in _rev_b().read_text(encoding="utf-8")
