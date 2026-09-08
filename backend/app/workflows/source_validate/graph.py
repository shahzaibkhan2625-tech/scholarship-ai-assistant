"""`source_validate` LangGraph workflow (T086, Blueprint §10, §29) —
`candidate_source -> reachability check -> type/reliability heuristics ->
[HUMAN APPROVAL GATE] -> promote to source_registry -> END`.

The reachability check calls the raw `web_fetch` tool directly, not the
`official_fetch` connector (T073) — that connector is gated on an *active*
`source_registry` row (constitution Principle IV), and a row that is still a
pending `candidate_source` does not have one yet, so it would always raise
`SourceNotApprovedError` before any network call.

The human-approval gate is a hard stop: `_human_gate_node` only ever routes
to `promote` when the invoking state explicitly carries `approved=True`. No
combination of reachability or heuristic results can substitute for that —
the graph pauses (`status="pending_approval"`) on every other input, and
`source_repo.approve_candidate_source` is only ever reached via that one
routed edge.
"""

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.data.repositories import source_repo
from app.schemas.source import SourceRegistryCreate
from app.tools.web_fetch import fetch_url

__all__ = ["SourceValidateState", "run_source_validate"]


class SourceValidateState(TypedDict, total=False):
    db: Any
    candidate_id: uuid.UUID
    url: str
    approved: bool
    reviewed_by: uuid.UUID | None
    source_data: SourceRegistryCreate | None
    reachable: bool
    reachability_error: str | None
    heuristics: dict
    status: str
    approved_source: Any


def _reachability_node(state: SourceValidateState) -> dict:
    result = fetch_url(state["url"])
    return {"reachable": result.success, "reachability_error": result.error}


def _heuristics_node(state: SourceValidateState) -> dict:
    url = state["url"].lower()
    host = url.split("://", 1)[-1].split("/", 1)[0]
    heuristics = {
        "https": url.startswith("https://"),
        "trusted_tld": any(host.endswith(tld) for tld in (".gov", ".edu", ".ac.uk", ".org")),
    }
    return {"heuristics": heuristics}


def _human_gate_node(state: SourceValidateState) -> dict:
    if not state.get("approved"):
        return {"status": "pending_approval"}
    return {"status": "approved"}


def _route_after_gate(state: SourceValidateState) -> str:
    return "promote" if state.get("status") == "approved" else "end"


def _promote_node(state: SourceValidateState) -> dict:
    db = state["db"]
    approved_source = source_repo.approve_candidate_source(
        db, state["candidate_id"], state["reviewed_by"], state["source_data"]
    )
    return {"approved_source": approved_source, "status": "promoted"}


def _build_graph():
    graph = StateGraph(SourceValidateState)
    graph.add_node("reachability", _reachability_node)
    graph.add_node("heuristics", _heuristics_node)
    graph.add_node("human_gate", _human_gate_node)
    graph.add_node("promote", _promote_node)

    graph.add_edge(START, "reachability")
    graph.add_edge("reachability", "heuristics")
    graph.add_edge("heuristics", "human_gate")
    graph.add_conditional_edges("human_gate", _route_after_gate, {"promote": "promote", "end": END})
    graph.add_edge("promote", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_source_validate(
    db,
    candidate_id: uuid.UUID,
    url: str,
    *,
    approved: bool = False,
    reviewed_by: uuid.UUID | None = None,
    source_data: SourceRegistryCreate | None = None,
) -> dict:
    initial_state: SourceValidateState = {
        "db": db,
        "candidate_id": candidate_id,
        "url": url,
        "approved": approved,
        "reviewed_by": reviewed_by,
        "source_data": source_data,
    }
    return _COMPILED_GRAPH.invoke(initial_state)
