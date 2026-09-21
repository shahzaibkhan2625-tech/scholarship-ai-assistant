"""FR-APP-4 / constitution Principle III: no live external submission exists
anywhere in the API surface yet (T117). Two independent layers, both
required — neither alone proves the absence of a live-send path:

1. ROUTE LAYER: no registered route exposes a live external submission path.
   A naive `route.path` scan over `app.routes` is not acceptable — this
   FastAPI version wraps every `include_router` call in an opaque
   `_IncludedRouter`, so a direct scan silently matches nothing regardless of
   what is actually registered (the exact false-positive class that let an
   undefined route pass as "correctly absent" in Phase 2A). This file reuses
   `find_registered_routes`'s prefix-joining technique, generalized to
   enumerate every route rather than one method+path at a time.

2. SOURCE LAYER: a live-send call could hide inside a service, workflow, or
   tool with no route at all, so the route layer can't prove absence by
   itself. This statically AST-scans `backend/app/` for outbound-send calls
   (`httpx`/`requests` `.post`/`.put`/`.request`, a `Session`/`Client` bound
   to either, or a browser-automation import) and fails if one is found
   anywhere outside the explicitly-excluded, explicitly-justified packages.

This test must stay green for every remaining Phase 4 slice (T121-T129):
when `submit_prep` (T123) and the submission-approval endpoints (T127) land,
they must keep staging materials for human-approved sending only, never add
a real outbound send.
"""

import ast
import re
from pathlib import Path

from tests.conftest import find_registered_routes

APP_ROOT = Path(__file__).resolve().parents[2] / "app"

# Packages whose entire purpose is legitimate outbound *reads* (GET) of
# scholarship data from sources already governed by the Source Registry
# (constitution Principle IV) — not a submission code path. Excluding them
# is safe precisely because they can never carry a `.post`/`.put`: they only
# ever fetch (verified by this same test's source-layer scan not tripping on
# `app/tools/web_fetch.py`, which stays in-scope and is GET-only). Excluding
# the directory outright (rather than relying on that alone) keeps the
# distinction this test cares about explicit: "read an official page" is not
# "send application materials," which is exactly what FR-APP-3/4 governs.
EXCLUDED_DIRS: set[Path] = {
    APP_ROOT / "sources",
}

_HTTP_MODULE_NAMES = {"httpx", "requests"}
_HTTP_CLIENT_FACTORY_ATTRS = {"Client", "AsyncClient", "Session"}
_OUTBOUND_SEND_ATTRS = {"post", "put", "request", "send"}
_BROWSER_AUTOMATION_MODULES = {
    "playwright",
    "playwright.sync_api",
    "playwright.async_api",
    "selenium",
    "pyppeteer",
}


def _iter_scanned_py_files():
    for path in sorted(APP_ROOT.rglob("*.py")):
        if any(excluded == path or excluded in path.parents for excluded in EXCLUDED_DIRS):
            continue
        yield path


def _imported_top_level_modules(tree: ast.Module) -> set[str]:
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.add(node.module.split(".")[0])
    return modules


def _root_name(node: ast.AST) -> str | None:
    """Walks an attribute/call chain down to its root `Name`, e.g.
    `httpx.Client().post` -> `httpx`, `router.post` -> `router`."""
    while isinstance(node, (ast.Attribute, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _http_client_variable_names(tree: ast.Module) -> set[str]:
    """Variables assigned from `httpx.Client(...)` / `httpx.AsyncClient(...)`
    / `requests.Session(...)` (however imported/aliased) — a later
    `<var>.post(...)` on one of these is exactly as much an outbound send as
    calling `httpx.post(...)` directly."""
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)):
            continue
        if value.func.attr not in _HTTP_CLIENT_FACTORY_ATTRS:
            continue
        if _root_name(value.func.value) not in _HTTP_MODULE_NAMES:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _find_outbound_send_calls(source: str, tree: ast.Module) -> list[str]:
    client_vars = _http_client_variable_names(tree)
    violations = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _OUTBOUND_SEND_ATTRS:
            continue
        root = _root_name(node.func.value)
        if root in _HTTP_MODULE_NAMES or root in client_vars:
            segment = ast.get_source_segment(source, node) or f"<{node.func.attr} call>"
            violations.append(f"line {node.lineno}: {segment}")
    return violations


def test_no_registered_route_exposes_a_live_external_submission_path() -> None:
    """Route layer. There is no live-send endpoint in this MVP at all
    (FR-APP-4) — not even under the staging `submission-approvals` path
    (which only ever records an approval row, T127, not yet built either).
    Enumerates every registered route (properly unwrapped from
    `include_router`'s opaque container) and asserts none of them matches a
    live-submission-shaped path for a mutating method."""
    from app.main import app

    live_submission_path_patterns = [
        re.compile(r".*/submit(?!ted)([-_/].*)?$"),
        re.compile(r".*/transmit.*"),
        re.compile(r".*/portal.*"),
        re.compile(r".*/external-submit.*"),
    ]

    matches = []
    for method in ("POST", "PUT", "PATCH"):
        for pattern in live_submission_path_patterns:
            matches.extend(find_registered_routes(app, method, pattern))

    assert matches == [], (
        f"Found route(s) shaped like a live external submission path: {matches} "
        "(FR-APP-4: no live-send endpoint exists in the MVP)"
    )


def test_no_outbound_send_call_exists_in_app_source() -> None:
    """Source layer — see module docstring. Walks every `.py` file under
    `backend/app/` (minus `EXCLUDED_DIRS`, justified above), AST-parses it,
    and fails if it imports a browser-automation library or contains an
    `httpx`/`requests` `.post`/`.put`/`.request`/`.send` call (directly on
    the module, or on a variable built from `Client`/`AsyncClient`/
    `Session`)."""
    violations = []

    for path in _iter_scanned_py_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))

        browser_modules = _imported_top_level_modules(tree) & _BROWSER_AUTOMATION_MODULES
        if browser_modules:
            violations.append(f"{path}: imports browser-automation module(s) {sorted(browser_modules)}")

        for detail in _find_outbound_send_calls(source, tree):
            violations.append(f"{path}: {detail}")

    assert violations == [], "Found potential live-submission code path(s):\n" + "\n".join(violations)


def test_excluded_dirs_are_read_only_fetch_and_would_be_caught_if_not() -> None:
    """Guards the exclusion itself: if `app/sources/` ever grows a real
    `.post`/`.put` outbound call, this must fail loudly rather than silently
    staying excluded — proving the exclusion is not a blanket carve-out."""
    for excluded in EXCLUDED_DIRS:
        assert excluded.is_dir(), f"{excluded} no longer exists; remove its EXCLUDED_DIRS entry"
        for path in sorted(excluded.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            violations = _find_outbound_send_calls(source, tree)
            assert violations == [], (
                f"{path} (excluded as 'legitimate fetch only') actually contains outbound-send call(s): "
                f"{violations} — narrow EXCLUDED_DIRS or move this file back into the scanned scope"
            )

