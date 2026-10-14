"""Contract-drift test: specs/002-.../contracts/openapi.yaml vs the live `app.openapi()` (T246).

Scope is feature 002 only: it never reads or edits 001's contract. For every path/method the
002 contract declares, and every schema in its `components`, the live schema must agree on:
  - the operation exists (path-parameter *names* are not wire-visible and are normalised);
  - the success status code, query parameters and response schema;
  - schema property names (both directions), `required`, enums/consts, types, nullability.

Known, deliberate, wire-equivalent differences are encoded as rules below rather than ignored:
  - Pydantic leaves a field out of `required` when it has a default or is nullable, but the
    serializer still always emits it. A contract-required field may therefore be optional live
    *only if* it is nullable or carries a default, and no route uses `response_model_exclude*`
    (which would make "always emitted" false). A live-required field must be contract-required.
  - `Literal[False]` renders as `const: false`, which equals the contract's `enum: [false]`.
  - Contract schema names that differ from the FastAPI model names are mapped in SCHEMA_NAMES.
Each rule has a negative test, so the comparator cannot pass vacuously.
"""

import copy
import re
from pathlib import Path

import yaml

from app.main import app

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "specs" / "002-source-monitoring-hardening" / "contracts" / "openapi.yaml"
API_DIR = Path(__file__).resolve().parents[2] / "app" / "api"

# contract schema name -> live (FastAPI model) name; absent means identical.
SCHEMA_NAMES = {
    "MonitoringRun": "MonitoringRunRead",
    "Alert": "AlertRead",
    "AlertPreferences": "AlertPreferencesRead",
    "CandidateSourceValidation": "CandidateSourceValidationRead",
}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def _norm_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _ref_name(node: dict) -> str | None:
    ref = node.get("$ref") if isinstance(node, dict) else None
    return ref.split("/")[-1] if ref else None


def _resolve(doc: dict, node: dict) -> dict:
    while isinstance(node, dict) and "$ref" in node:
        node = doc["components"]["schemas"][_ref_name(node)]
    return node


def _flatten(doc: dict, node: dict) -> tuple[dict, set[str]]:
    """Properties and required names of a schema, merging `allOf` parts."""
    node = _resolve(doc, node)
    props: dict = {}
    required: set[str] = set()
    for part in node.get("allOf", []):
        part_props, part_required = _flatten(doc, part)
        props.update(part_props)
        required |= part_required
    props.update(node.get("properties", {}))
    required |= set(node.get("required", []))
    return props, required


def _describe(doc: dict, node: dict) -> dict:
    """Comparable summary of a property: type, enum (const folded in), format, nullable, items."""
    node = _resolve(doc, node)
    nullable = bool(node.get("nullable"))
    if "anyOf" in node:
        options = [_resolve(doc, option) for option in node["anyOf"]]
        nullable = nullable or any(option.get("type") == "null" for option in options)
        non_null = [option for option in options if option.get("type") != "null"]
        node = non_null[0] if non_null else {}
    enum = node.get("enum")
    if enum is None and "const" in node:
        enum = [node["const"]]
    return {
        "type": node.get("type"),
        "enum": sorted(enum, key=str) if enum else None,
        "format": node.get("format"),
        "nullable": nullable,
        "items": _describe(doc, node["items"]) if "items" in node else None,
    }


def _always_emitted(doc: dict, prop: dict) -> bool:
    """A live property that is optional in `required` but still always serialized."""
    return "default" in prop or _describe(doc, prop)["nullable"]


def schema_drift(contract: dict, live: dict, contract_name: str) -> list[str]:
    live_name = SCHEMA_NAMES.get(contract_name, contract_name)
    if live_name not in live["components"]["schemas"]:
        return [f"{contract_name}: live schema {live_name!r} does not exist"]
    c_props, c_required = _flatten(contract, contract["components"]["schemas"][contract_name])
    l_props, l_required = _flatten(live, live["components"]["schemas"][live_name])
    problems: list[str] = []
    if set(c_props) != set(l_props):
        problems.append(
            f"{contract_name}: properties differ (contract-only {sorted(set(c_props) - set(l_props))}, "
            f"live-only {sorted(set(l_props) - set(c_props))})"
        )
    for name in sorted(c_required - l_required):
        if name in l_props and not _always_emitted(live, l_props[name]):
            problems.append(f"{contract_name}.{name}: required in contract but may be omitted live")
    for name in sorted(l_required - c_required):
        problems.append(f"{contract_name}.{name}: required live but optional in contract")
    for name in sorted(set(c_props) & set(l_props)):
        c_desc, l_desc = _describe(contract, c_props[name]), _describe(live, l_props[name])
        if c_desc != l_desc:
            problems.append(f"{contract_name}.{name}: contract {c_desc} != live {l_desc}")
    return problems


def _success_codes(operation: dict) -> set[str]:
    return {str(code) for code in operation.get("responses", {}) if str(code).startswith("2")}


def _response_schema(operation: dict, code: str, *, rename: bool) -> str | None:
    """Name of the JSON response schema ("[Name]" for a list); `rename` maps contract names to live."""
    content = operation.get("responses", {}).get(code, {}).get("content", {})
    schema = content.get("application/json", {}).get("schema", {})

    def name_of(node: dict) -> str:
        name = _ref_name(node)
        return SCHEMA_NAMES.get(name, name) if rename else name

    if _ref_name(schema):
        return name_of(schema)
    if schema.get("type") == "array" and _ref_name(schema.get("items", {})):
        return "[" + name_of(schema["items"]) + "]"
    return None


def _query_params(operation: dict) -> dict[str, bool]:
    return {p["name"]: bool(p.get("required")) for p in operation.get("parameters", []) if p.get("in") == "query"}


def operation_drift(contract: dict, live: dict) -> list[str]:
    live_paths = {_norm_path(path): item for path, item in live["paths"].items()}
    problems: list[str] = []
    for path, item in contract["paths"].items():
        live_item = live_paths.get(_norm_path(path))
        if live_item is None:
            problems.append(f"{path}: path missing live")
            continue
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            tag = f"{method.upper()} {path}"
            live_op = live_item.get(method)
            if live_op is None:
                problems.append(f"{tag}: method missing live")
                continue
            c_codes, l_codes = _success_codes(operation), _success_codes(live_op)
            if c_codes != l_codes:
                problems.append(f"{tag}: success status {sorted(c_codes)} != live {sorted(l_codes)}")
            for code in c_codes & l_codes:
                c_schema = _response_schema(operation, code, rename=True)
                l_schema = _response_schema(live_op, code, rename=False)
                if c_schema is not None and c_schema != l_schema:
                    problems.append(f"{tag} {code}: response schema {c_schema} != live {l_schema}")
            c_query, l_query = _query_params(operation), _query_params(live_op)
            if set(c_query) != set(l_query):
                problems.append(f"{tag}: query params {sorted(c_query)} != live {sorted(l_query)}")
            for name in set(c_query) & set(l_query):
                if c_query[name] != l_query[name]:
                    problems.append(f"{tag}: query param {name!r} required-ness differs")
    return problems


def test_every_contract_operation_exists_live_with_matching_shape():
    assert operation_drift(_contract(), app.openapi()) == []


def test_every_contract_schema_matches_the_live_schema():
    contract, live = _contract(), app.openapi()
    problems = [p for name in contract["components"]["schemas"] for p in schema_drift(contract, live, name)]
    assert problems == []


def test_the_contract_declares_what_this_test_expects_to_cover():
    contract = _contract()
    assert len(contract["paths"]) >= 10 and len(contract["components"]["schemas"]) >= 10


def test_no_route_trims_defaulted_or_none_fields_from_responses():
    """The 'optional live but always emitted' rule is only sound while nothing excludes fields."""
    offenders = [
        path.name
        for path in API_DIR.glob("*.py")
        if re.search(r"response_model_exclude(_none|_unset|_defaults)?\s*=", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


# -- negative tests: the comparator must notice each kind of drift ------------------------


def _live_with(mutate) -> dict:
    live = copy.deepcopy(app.openapi())
    mutate(live)
    return live


def test_detects_a_missing_route():
    live = _live_with(lambda d: d["paths"].pop("/coverage"))
    assert any("/coverage" in p for p in operation_drift(_contract(), live))


def test_detects_a_changed_success_status():
    def mutate(d):
        responses = d["paths"]["/monitoring/runs"]["post"]["responses"]
        responses["200"] = responses.pop("202")

    live = _live_with(mutate)
    assert any("success status" in p for p in operation_drift(_contract(), live))


def test_detects_a_removed_property():
    live = _live_with(lambda d: d["components"]["schemas"]["SourceHealth"]["properties"].pop("status"))
    assert any("properties differ" in p for p in schema_drift(_contract(), live, "SourceHealth"))


def test_detects_a_field_that_became_required_live():
    live = _live_with(lambda d: d["components"]["schemas"]["SourceHealth"].setdefault("required", []).append("listing_page_url"))
    assert any("required live" in p for p in schema_drift(_contract(), live, "SourceHealth"))


def test_detects_a_changed_enum():
    def mutate(d):
        d["components"]["schemas"]["MonitoringTrigger"]["enum"] = ["scheduled"]

    assert any("MonitoringRun.trigger" in p for p in schema_drift(_contract(), _live_with(mutate), "MonitoringRun"))


def test_detects_a_required_contract_field_that_is_not_always_emitted():
    def mutate(d):
        d["components"]["schemas"]["SourceHealth"]["required"] = [
            r for r in d["components"]["schemas"]["SourceHealth"]["required"] if r != "name"
        ]

    assert any("may be omitted" in p for p in schema_drift(_contract(), _live_with(mutate), "SourceHealth"))
