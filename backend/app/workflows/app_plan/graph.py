"""`app_plan` LangGraph workflow (T122; Blueprint §17, data-model.md §8,
FR-PLAN-1/2) — `load_application -> resolve_checklist -> sort_checklist ->
persist -> END`.

NO LLM CALL ANYWHERE IN THIS MODULE. Agentic mode (T124, later) and
deterministic mode (T126's endpoint) must produce identical checklists for
the same inputs; a shared implementation is necessary but not sufficient —
if it contained a nondeterministic LLM call, the same function run twice
could return different checklists. This workflow, plus the pure
`readiness.resolve_readiness_label` it calls, is what makes parity
structural rather than lucky.

`run_app_plan` is the ONLY public entry point. The deterministic-mode
endpoint (T126) calls it directly; the agent (T124) will call the exact same
function — no separate agent-facing variant exists or should be added.

Ordering never relies on database row order (Postgres may return rows in a
different order on each query): `sort_checklist` applies the approved A3 key
`(not mandatory, category, key, str(requirement_id))` before persistence, so
re-running on unchanged state produces an identical, identically-ordered
plan. `replace_tasks_for_application` (Phase 4, T118/T120) exists for
exactly this: a full replace in one transaction, never a duplicate append.
"""

import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.data.repositories import application_repo
from app.schemas.plan import ApplicationPlan, ChecklistItem
from app.services.readiness import ChecklistItemDraft, compute_checklist_for_application

__all__ = ["AppPlanState", "run_app_plan"]


class AppPlanState(TypedDict, total=False):
    db: Any
    user_id: uuid.UUID
    application_id: uuid.UUID
    application: Any
    drafts: list[ChecklistItemDraft]
    sorted_drafts: list[ChecklistItemDraft]
    plan: ApplicationPlan | None
    error: str | None


def _load_application_node(state: AppPlanState) -> dict:
    application = application_repo.get_by_id_for_user(state["db"], state["user_id"], state["application_id"])
    if application is None:
        return {"error": "not_found"}
    return {"application": application}


def _resolve_checklist_node(state: AppPlanState) -> dict:
    if state.get("error"):
        return {}
    drafts = compute_checklist_for_application(state["db"], state["user_id"], state["application"])
    return {"drafts": drafts}


def _sort_checklist_node(state: AppPlanState) -> dict:
    """Approved A3 sort key: mandatory items first, then alphabetical by
    category/key, with the requirement UUID as a final deterministic
    tiebreaker — never DB row order."""
    if state.get("error"):
        return {}
    drafts = state.get("drafts") or []
    sorted_drafts = sorted(
        drafts,
        key=lambda d: (not d.mandatory, d.category, d.description, str(d.requirement_id)),
    )
    return {"sorted_drafts": sorted_drafts}


def _persist_node(state: AppPlanState) -> dict:
    """The only node that touches the DB for writes. `replace_tasks_for_
    application`'s delete+insert-in-one-transaction guarantees idempotency:
    re-running on unchanged inputs replaces the prior task set with an
    identical one rather than duplicating it."""
    if state.get("error"):
        return {}
    sorted_drafts = state.get("sorted_drafts") or []
    task_dicts = [
        {
            "requirement_id": draft.requirement_id,
            "description": draft.description,
            "category": draft.category,
            "readiness_label": draft.readiness_label,
            "due_date": draft.due_date,
        }
        for draft in sorted_drafts
    ]
    tasks = application_repo.replace_tasks_for_application(
        state["db"], state["user_id"], state["application_id"], task_dicts
    )
    checklist = [
        ChecklistItem(
            description=task.description,
            category=task.category,
            readiness_label=task.readiness_label,
            due_date=task.due_date,
            requirement_id=task.requirement_id,
        )
        for task in tasks
    ]
    plan = ApplicationPlan(application_id=state["application_id"], checklist=checklist)
    return {"plan": plan}


def _build_graph():
    graph = StateGraph(AppPlanState)
    graph.add_node("load_application", _load_application_node)
    graph.add_node("resolve_checklist", _resolve_checklist_node)
    graph.add_node("sort_checklist", _sort_checklist_node)
    graph.add_node("persist", _persist_node)

    graph.add_edge(START, "load_application")
    graph.add_edge("load_application", "resolve_checklist")
    graph.add_edge("resolve_checklist", "sort_checklist")
    graph.add_edge("sort_checklist", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_app_plan(db, user_id: uuid.UUID, application_id: uuid.UUID) -> ApplicationPlan | None:
    """The single shared entry point for both deterministic and (later)
    agentic mode. Returns None when `application_id` is not owned by
    `user_id` (or doesn't exist) — the caller maps that to 404."""
    initial_state: AppPlanState = {
        "db": db,
        "user_id": user_id,
        "application_id": application_id,
    }
    final_state = _COMPILED_GRAPH.invoke(initial_state)
    if final_state.get("error"):
        return None
    return final_state["plan"]
