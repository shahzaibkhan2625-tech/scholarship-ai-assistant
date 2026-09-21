"""`submit_prep` LangGraph workflow (T123; ADR-0004, constitution Principle
III, FR-APP-3/FR-APP-4) -- `load_application -> assemble_package ->
compute_fingerprint -> [APPROVAL GATE] check_approval ->
[FINAL APPROVAL GATE] map_for_staging -> END`.

NO LLM CALL ANYWHERE IN THIS MODULE, and NO OUTBOUND SEND ANYWHERE IN THIS
MODULE. This workflow's entire job is to assemble the submission package,
fingerprint it, check it against an already-recorded human approval, and --
only if authorized -- map it into a staged, human-reviewable shape. It always
stops there. There is no code path here (or anywhere else in `app/`) that
transmits to an external portal; `tests/api/test_no_live_submission.py`
(T117) statically enforces that for the whole `app/` tree, this module
included.

The two gates are both stop-and-return-control points, never conditional
forks that could route around a missing/mismatched approval and continue
anyway:

  * [APPROVAL GATE] `check_approval` -- runs the T116 scope-check
    (`is_submission_authorized`) against the approvals already on file for
    this application. Not authorized -> the graph short-circuits straight to
    END with `staged=None`, the same short-circuit style `app_plan/graph.py`
    uses for `error`. This is the constitution's "approval scoped to the
    exact submission" gate: an approval for a different `submission_scope`,
    or for the same scope but different `content_fingerprint` (content
    changed since it was approved), never lets this gate pass.

  * [FINAL APPROVAL GATE] `map_for_staging` -- reached only when
    `check_approval` authorized the attempt. It assembles the final staged
    output and returns it -- and that is *all* it does. Per FR-APP-4, live
    autonomous transmission is out of MVP scope; this node's existence is
    itself the "final review opportunity immediately before the irreversible
    submit action" (Principle III) expressed structurally: there is no
    submit action after it to fall into, only a staged package handed back
    to the caller for a human to look at.

`run_submit_prep` is the single public entry point, mirroring
`run_app_plan`'s shape: one function, no separate agent-facing variant, so a
future agentic mode (T124, out of scope here) calls exactly this.
"""

import hashlib
import json
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.data.repositories import application_repo, document_repo
from app.services.submission_approval import is_submission_authorized

__all__ = ["SubmitPrepState", "compute_content_fingerprint", "run_submit_prep"]


def compute_content_fingerprint(application_documents: list[Any], generated_documents: list[Any]) -> str:
    """The approved A1 algorithm: SHA-256 of a canonical (sorted,
    separator-normalised) JSON serialization of the assembled submission
    package's content-identifying fields.

    Included, per document:
      * `ApplicationDocument` -> `id`, `type`, `satisfies_requirement_id`,
        `checksum`. `checksum` is set once at upload and never mutated
        (`document_repo.update_application_document` never touches it), so
        it stands in for the uploaded bytes without re-reading the file.
      * `GeneratedDocument` -> `id`, `type`.

    Deliberately excluded: `uploaded_at`/`generated_at` (timestamps, never
    content), `file_ref` (a storage key, not the content itself),
    `parsed_meta`/`inconsistency_flags` (annotations about a document, not
    its submitted substance), and DB row order (Postgres gives no ordering
    guarantee -- the explicit `sorted(...)` calls below are what make this
    canonical, not query order).

    Documented assumption: `GeneratedDocument` identity is captured by `id`
    alone ONLY because `generated_documents` is append-only today -- per
    `app/models/application.py`'s docstring and the absence of any update
    function in `document_repo.py`, a regeneration always creates a new row
    with a new `id`, so `id` already changes whenever the generated content
    changes. If an in-place edit path for generated documents is ever added,
    `id` alone would stop reflecting content, and this hash would need the
    generated content itself (e.g. a checksum column) added alongside it.

    Fails toward the SAFE direction: every included field changes only when
    real submission content changes (new/removed/reclassified document, new
    generation) or is a deliberately-conservative inclusion of a
    borderline field (`type`, `satisfies_requirement_id`) that could in
    principle be considered "metadata." If one of those borderline fields
    turns out not to matter, the cost is an unnecessary re-approval prompt
    (false invalidation -- annoying, safe) -- never a stale approval silently
    covering changed content (false validation -- dangerous), since nothing
    volatile or cosmetic (timestamps, file storage keys, row order) is
    included at all.
    """
    package = {
        "application_documents": sorted(
            (
                {
                    "id": str(doc.id),
                    "type": doc.type.value,
                    "satisfies_requirement_id": (
                        str(doc.satisfies_requirement_id) if doc.satisfies_requirement_id else None
                    ),
                    "checksum": doc.checksum,
                }
                for doc in application_documents
            ),
            key=lambda row: row["id"],
        ),
        "generated_documents": sorted(
            ({"id": str(doc.id), "type": doc.type.value} for doc in generated_documents),
            key=lambda row: row["id"],
        ),
    }
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SubmitPrepState(TypedDict, total=False):
    db: Any
    user_id: uuid.UUID
    application_id: uuid.UUID
    submission_scope: str
    application: Any
    application_documents: list[Any]
    generated_documents: list[Any]
    content_fingerprint: str
    authorized: bool
    staged: dict | None
    error: str | None


def _load_application_node(state: SubmitPrepState) -> dict:
    application = application_repo.get_by_id_for_user(state["db"], state["user_id"], state["application_id"])
    if application is None:
        return {"error": "not_found"}
    return {"application": application}


def _assemble_package_node(state: SubmitPrepState) -> dict:
    if state.get("error"):
        return {}
    application_documents = document_repo.list_application_documents_for_application(
        state["db"], state["user_id"], state["application_id"]
    )
    generated_documents = document_repo.list_generated_documents_for_application(
        state["db"], state["user_id"], state["application_id"]
    )
    return {"application_documents": application_documents, "generated_documents": generated_documents}


def _compute_fingerprint_node(state: SubmitPrepState) -> dict:
    if state.get("error"):
        return {}
    fingerprint = compute_content_fingerprint(
        state.get("application_documents") or [], state.get("generated_documents") or []
    )
    return {"content_fingerprint": fingerprint}


def _check_approval_node(state: SubmitPrepState) -> dict:
    """[APPROVAL GATE] -- stops here (never proceeds to staging) unless a
    stored approval matches BOTH `submission_scope` and the just-computed
    `content_fingerprint` (T116's `is_submission_authorized`)."""
    if state.get("error"):
        return {}
    approvals = application_repo.list_submission_approvals(state["db"], state["user_id"], state["application_id"])
    authorized = is_submission_authorized(approvals, state["submission_scope"], state["content_fingerprint"])
    return {"authorized": authorized}


def _map_for_staging_node(state: SubmitPrepState) -> dict:
    """[FINAL APPROVAL GATE] -- assemble + map the authorized package into a
    staged, human-reviewable shape, then stop. This function does not send
    anything anywhere; there is no node after it and no external client used
    here."""
    if state.get("error") or not state.get("authorized"):
        return {}
    staged = {
        "application_id": str(state["application_id"]),
        "submission_scope": state["submission_scope"],
        "content_fingerprint": state["content_fingerprint"],
        "application_documents": [
            {
                "id": str(doc.id),
                "type": doc.type.value,
                "satisfies_requirement_id": (
                    str(doc.satisfies_requirement_id) if doc.satisfies_requirement_id else None
                ),
            }
            for doc in sorted(state.get("application_documents") or [], key=lambda d: str(d.id))
        ],
        "generated_documents": [
            {"id": str(doc.id), "type": doc.type.value}
            for doc in sorted(state.get("generated_documents") or [], key=lambda d: str(d.id))
        ],
    }
    return {"staged": staged}


def _build_graph():
    graph = StateGraph(SubmitPrepState)
    graph.add_node("load_application", _load_application_node)
    graph.add_node("assemble_package", _assemble_package_node)
    graph.add_node("compute_fingerprint", _compute_fingerprint_node)
    graph.add_node("check_approval", _check_approval_node)
    graph.add_node("map_for_staging", _map_for_staging_node)

    graph.add_edge(START, "load_application")
    graph.add_edge("load_application", "assemble_package")
    graph.add_edge("assemble_package", "compute_fingerprint")
    graph.add_edge("compute_fingerprint", "check_approval")
    graph.add_edge("check_approval", "map_for_staging")
    graph.add_edge("map_for_staging", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_submit_prep(
    db, user_id: uuid.UUID, application_id: uuid.UUID, submission_scope: str
) -> dict | None:
    """The single shared entry point (deterministic mode now; the future
    agentic mode, T124, out of scope here, would call this exact function).

    Returns `None` when `application_id` is not owned by `user_id` (or
    doesn't exist) -- the caller maps that to 404, same convention as
    `run_app_plan`. Returns a dict with `staged=None` and `authorized=False`
    when no matching approval is on file yet (or the content has since
    changed) -- never raises and never sends anything. Returns a dict with
    the staged package and `authorized=True` only once a matching approval
    exists for both the exact scope and the exact content fingerprint."""
    initial_state: SubmitPrepState = {
        "db": db,
        "user_id": user_id,
        "application_id": application_id,
        "submission_scope": submission_scope,
    }
    final_state = _COMPILED_GRAPH.invoke(initial_state)
    if final_state.get("error"):
        return None
    return {
        "content_fingerprint": final_state["content_fingerprint"],
        "authorized": final_state.get("authorized", False),
        "staged": final_state.get("staged"),
    }
