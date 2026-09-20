"""`sop_gen` LangGraph workflow (T110, Blueprint §16) — `gather -> outline ->
draft -> ground_check -> polish -> render -> END`.

Mirrors `app/workflows/cv_gen/graph.py`'s grounding discipline exactly: the
allowed-facts pool (`gather`) is the same three sources and nothing else —
profile, uploaded documents' extracted fields, and the scholarship's verified
requirements — so the private fact-gathering helpers are reused unchanged
from `cv_gen` rather than re-implemented (a second, drift-prone copy of the
same "what counts as a fact" logic would be worse than importing it).

**`outline` derives the scholarship's own SOP/motivation questions from its
verified requirements/fields** (BP §16): a requirement or field whose key
names an SOP/motivation/essay/personal-statement question set drives the
exact questions the draft must answer. Nothing is ever assumed — when no such
requirement exists, `outline` yields no questions and `draft` writes a single
generic personal statement instead of guessing what a "standard" SOP would
ask.

**`ground_check` is a GATE**, identical in discipline to `cv_gen`: a single
untraceable claim halts the whole run, reported (which claim/question, why),
never silently dropped.

**`polish` runs AFTER `ground_check`, and may only rephrase — never add a new
factual claim.** This is enforced, not just documented: every polished
answer is re-verified against the exact same allowed-facts pool via
`verify_claim_grounded` before `render` ever sees it. A polish attempt that
fails to parse falls back to the already-gated pre-polish text (safe — that
text already passed the gate); a polish attempt that *parses* but introduces
something ungrounded blocks the whole run exactly like a `ground_check`
failure would, because that is what it is — the gate applies to whatever
text is about to be rendered, not only to the first draft.

`render` populates `generated_documents.source_trace` for every claim that
survived (post-polish) — a generated SOP with an empty `source_trace` is a
bug, and `render` never runs at all when the gate is blocked."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from app.data.files.storage import get_storage
from app.data.repositories import document_repo
from app.models.application import Application
from app.models.document import ApplicationDocument, GeneratedDocument, GeneratedDocumentType
from app.tools._json_utils import ExtractionParseError, extract_json_object
from app.tools.ground_check import verify_claim_grounded
from app.workflows.cv_gen.graph import (
    AllowedFact,
    _document_facts,
    _profile_facts,
    _requirement_facts,
)

__all__ = ["SopGenState", "SopDraft", "SopDraftClaim", "run_sop_gen"]

_GENERIC_QUESTION = "Personal Statement"


class SopDraftClaim(BaseModel):
    question: str = _GENERIC_QUESTION
    text: str


class SopDraft(BaseModel):
    claims: list[SopDraftClaim] = Field(default_factory=list)


class SopDraftError(Exception):
    """Raised when the LLM fails to produce schema-valid draft JSON after the
    reject-and-retry budget is exhausted (mirrors `CvDraftError`)."""


_SOP_QUESTION_KEY_MARKERS = ("sop_question", "motivation_question", "essay_question", "personal_statement_question")

_DRAFT_SYSTEM_INSTRUCTION = """You are drafting a scholarship applicant's Statement of Purpose (SOP) /
motivation essay. You may state ONLY facts explicitly given to you in the ALLOWED FACTS list below —
never invent, estimate, embellish, or infer any additional detail (no invented employers, dates,
achievements, publications, grades, motivations, or anecdotes). If the allowed facts are insufficient to
say something substantive for a question, write as little as the facts support rather than padding with
invented content.

Write ONE claim per underlying allowed fact you use, rather than weaving several facts together into one
long sentence. For each claim, prefix the fact with only "I have ", "I am ", or "My " and then state the
fact's own wording essentially verbatim (dropping only a leading category label like "Education:" or
"Name:") — e.g. a fact reading "Education: BS in Computer Science from MIT with GPA 3.8/4.0." becomes the
claim "I have a BS in Computer Science from MIT with GPA 3.8/4.0." Do not use any other verb (never
"earned", "achieved", "obtained", "completed", "holds", "possess", etc.), do not add an adjective or
descriptive noun of your own (never "the applicant", "the candidate", "successfully", etc.), and do not
substitute synonyms for a fact's specific terms or reformat its numbers.

Respond with ONLY a JSON object (no markdown, no commentary) matching exactly this shape:
{
  "claims": [
    {"question": string (repeat the exact question text given to you, or "%s" if none was given),
     "text": string}
  ]
}
Produce one claim per question AT MINIMUM — repeat the same "question" value across multiple claims when
you have more than one allowed fact relevant to that question, instead of merging them into a single
entry. Each "text" MUST be built only from the allowed facts."""

_POLISH_SYSTEM_INSTRUCTION = """You are lightly polishing already fact-checked SOP answers. Your ONLY
allowed changes are: fixing grammar/punctuation, and joining adjacent claims that share the same
"question" into one smoothly-punctuated paragraph (e.g. with ", and " or "; "). You MUST NOT add, remove,
exaggerate, or reword any fact, name, date, number, achievement, verb, adjective, or descriptive noun that
is not already present verbatim in the text you were given — do not add words like "successfully",
"achieved", "the applicant", or similar framing, and do not substitute a synonym for any specific term.
When in doubt, make NO change and return the claim exactly as given.

Respond with ONLY a JSON object (no markdown, no commentary) matching exactly this shape:
{
  "claims": [
    {"question": string (repeat exactly as given), "text": string}
  ]
}
Return exactly one claim per input claim, in the same order."""

_DEFAULT_MAX_ATTEMPTS = 2


def _extract_sop_questions(scholarship: Any) -> list[str]:
    """Derive the scholarship's own SOP/motivation questions from its
    verified requirements/fields — never a standard/assumed set (BP §16)."""
    if scholarship is None:
        return []

    questions: list[str] = []
    for source in (getattr(scholarship, "requirements", None) or []) + (getattr(scholarship, "fields", None) or []):
        if getattr(source, "value_status", None) != "known" or source.value is None:
            continue
        key = (source.key or "").lower()
        if not any(marker in key for marker in _SOP_QUESTION_KEY_MARKERS):
            continue

        value = source.value
        if isinstance(value, list):
            questions.extend(str(v).strip() for v in value if str(v).strip())
        elif isinstance(value, str):
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            questions.extend(lines if len(lines) > 1 else [value.strip()])

    return questions


def _draft_sop(facts: list[AllowedFact], questions: list[str], scholarship_name: str, *, max_attempts: int) -> SopDraft:
    from app.core.llm import generate_text

    facts_block = "\n".join(f"- {fact.text}" for fact in facts)
    questions_block = "\n".join(f"- {q}" for q in questions) if questions else f"- (none specified — write a single \"{_GENERIC_QUESTION}\" answer)"
    prompt = (
        f"Scholarship: {scholarship_name or 'unspecified'}\n\n"
        f"QUESTIONS TO ANSWER:\n{questions_block}\n\n"
        f"ALLOWED FACTS (the only facts you may state):\n{facts_block}"
    )
    system_instruction = _DRAFT_SYSTEM_INSTRUCTION % _GENERIC_QUESTION

    last_error: Exception | None = None
    for _ in range(max_attempts):
        raw = generate_text(prompt, system_instruction=system_instruction)
        try:
            parsed = extract_json_object(raw)
            return SopDraft(**parsed)
        except (ExtractionParseError, ValidationError) as exc:
            last_error = exc
            continue

    raise SopDraftError(f"Could not draft valid SOP content after {max_attempts} attempt(s): {last_error}")


def _polish_sop(claims: list[SopDraftClaim], *, max_attempts: int) -> list[SopDraftClaim] | None:
    """Returns the polished claims, or `None` if polishing could not produce
    parseable output — callers must fall back to the pre-polish (already
    gated) text in that case, never block generation over a formatting
    failure in a step whose only job is rephrasing."""
    from app.core.llm import generate_text

    claims_block = "\n".join(f'- question: "{c.question}"\n  text: "{c.text}"' for c in claims)
    prompt = f"APPROVED ANSWERS TO POLISH:\n{claims_block}"

    for _ in range(max_attempts):
        raw = generate_text(prompt, system_instruction=_POLISH_SYSTEM_INSTRUCTION)
        try:
            parsed = extract_json_object(raw)
            draft = SopDraft(**parsed)
            if len(draft.claims) == len(claims):
                return draft.claims
        except (ExtractionParseError, ValidationError):
            continue

    return None


class SopGenState(TypedDict, total=False):
    db: Any
    application: Application
    profile: Any
    documents: list[ApplicationDocument]
    scholarship: Any
    facts: list[AllowedFact]
    questions: list[str]
    draft: SopDraft | None
    source_trace: list[dict]
    gaps: list[dict]
    blocked: bool
    generated_document: GeneratedDocument | None
    rendered_text: str | None


def _gather_node(state: SopGenState) -> dict:
    facts = _profile_facts(state.get("profile")) + _document_facts(state.get("documents") or []) + _requirement_facts(state.get("scholarship"))
    return {"facts": facts}


def _outline_node(state: SopGenState) -> dict:
    return {"questions": _extract_sop_questions(state.get("scholarship"))}


def _draft_node(state: SopGenState) -> dict:
    facts = state.get("facts") or []
    if not facts:
        return {
            "draft": None,
            "gaps": [
                {
                    "claim": None,
                    "question": None,
                    "reason": "No profile, document, or verified-requirement information is available to draft an SOP from.",
                }
            ],
        }

    scholarship = state.get("scholarship")
    scholarship_name = getattr(scholarship, "name", "") or ""
    try:
        draft = _draft_sop(facts, state.get("questions") or [], scholarship_name, max_attempts=_DEFAULT_MAX_ATTEMPTS)
    except SopDraftError as exc:
        return {"draft": None, "gaps": [{"claim": None, "question": None, "reason": str(exc)}]}
    return {"draft": draft}


def _ground_check_node(state: SopGenState) -> dict:
    draft = state.get("draft")
    if draft is None:
        return {"blocked": True}

    facts = state.get("facts") or []
    evidence_texts = [fact.text for fact in facts]
    source_by_evidence = {fact.text: fact.source for fact in facts}

    gaps: list[dict] = []
    trace: list[dict] = []
    for claim in draft.claims:
        result = verify_claim_grounded(claim.text, evidence_texts)
        if not result.grounded:
            gaps.append(
                {
                    "claim": claim.text,
                    "question": claim.question,
                    "reason": "Not traceable to the profile, an uploaded document, or a verified scholarship requirement.",
                }
            )
            continue
        trace.append(
            {"claim": claim.text, "question": claim.question, "grounded_in": source_by_evidence.get(result.matched_evidence, "unknown")}
        )

    if gaps:
        return {"blocked": True, "gaps": gaps}
    return {"blocked": False, "source_trace": trace}


def _polish_node(state: SopGenState) -> dict:
    if state.get("blocked"):
        return {}

    draft = state["draft"]
    facts = state.get("facts") or []
    evidence_texts = [fact.text for fact in facts]
    source_by_evidence = {fact.text: fact.source for fact in facts}

    polished_claims = _polish_sop(draft.claims, max_attempts=_DEFAULT_MAX_ATTEMPTS)
    if polished_claims is None:
        # Polishing failed to produce usable output — keep the already-gated
        # pre-polish text unchanged. Safe: it already passed ground_check.
        return {}

    gaps: list[dict] = []
    trace: list[dict] = []
    for claim in polished_claims:
        result = verify_claim_grounded(claim.text, evidence_texts)
        if not result.grounded:
            gaps.append(
                {
                    "claim": claim.text,
                    "question": claim.question,
                    "reason": "Polishing introduced content not traceable to the allowed-facts pool.",
                }
            )
            continue
        trace.append(
            {"claim": claim.text, "question": claim.question, "grounded_in": source_by_evidence.get(result.matched_evidence, "unknown")}
        )

    if gaps:
        return {"blocked": True, "gaps": gaps}
    return {"draft": SopDraft(claims=polished_claims), "source_trace": trace}


def _render_node(state: SopGenState) -> dict:
    if state.get("blocked"):
        return {}

    draft = state["draft"]
    application = state["application"]

    lines = ["SOP", ""]
    for claim in draft.claims:
        lines.append(claim.question.upper())
        lines.append(claim.text)
        lines.append("")
    rendered_text = "\n".join(lines).strip() + "\n"

    storage = get_storage()
    file_ref = storage.save(application.user_id, "sop.txt", rendered_text.encode("utf-8"))

    document = document_repo.create_generated_document(
        state["db"],
        application.user_id,
        application.id,
        GeneratedDocumentType.SOP,
        file_ref,
        source_trace=state.get("source_trace") or [],
    )
    return {"generated_document": document, "rendered_text": rendered_text}


def _build_graph():
    graph = StateGraph(SopGenState)
    graph.add_node("gather", _gather_node)
    graph.add_node("outline", _outline_node)
    graph.add_node("draft", _draft_node)
    graph.add_node("ground_check", _ground_check_node)
    graph.add_node("polish", _polish_node)
    graph.add_node("render", _render_node)

    graph.add_edge(START, "gather")
    graph.add_edge("gather", "outline")
    graph.add_edge("outline", "draft")
    graph.add_edge("draft", "ground_check")
    graph.add_edge("ground_check", "polish")
    graph.add_edge("polish", "render")
    graph.add_edge("render", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_sop_gen(
    db,
    *,
    application: Application,
    profile: Any,
    documents: list[ApplicationDocument],
    scholarship: Any,
) -> SopGenState:
    initial_state: SopGenState = {
        "db": db,
        "application": application,
        "profile": profile,
        "documents": documents,
        "scholarship": scholarship,
    }
    return _COMPILED_GRAPH.invoke(initial_state)
