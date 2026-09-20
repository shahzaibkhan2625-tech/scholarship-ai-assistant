"""`cv_gen` LangGraph workflow (T109, Blueprint §16) — `gather -> select_format
-> draft -> ground_check -> render -> END`.

**The allowed-facts pool (`gather`) has exactly three sources and nothing
else**: the user's profile (`profiles`/`education_records`/`test_scores`/
`experience`), their uploaded documents' extracted fields
(`application_documents.parsed_meta`, populated by `doc_pipeline` — T105),
and the scholarship's verified requirements (`requirements`). Anything not in
that pool does not exist for generation purposes (FR-GEN-2).

**`ground_check` is a GATE, not a filter.** A single untraceable claim halts
the whole run — `render` never sees a draft with the bad claim quietly
removed. The gap is reported (which claim, why) so the user learns what the
system could not support, per US5 Scenario 5.

**Grounding is deterministic, not a second LLM call trusting itself** — this
mirrors `app/tools/ground_check.py`'s own "harness discipline" mandate (reused
unchanged from Q&A/US3, T019/T020; `verify_claim_grounded` layers numeric-
consistency on top without touching that tool's existing behaviour). `draft`
is the only LLM invocation in this workflow; the grounding decision is
categorically separate from it — not merely a second prompt — so the model
that wrote the draft can never also be the one that approves it.

`select_format` is requirement-aware (BP §16): an explicit `cv_format`
requirement or scholarship field drives the format (e.g. Europass); the
format is never assumed when nothing states it.

`render` populates `generated_documents.source_trace` with, for every claim
that survived the gate, the exact profile field / document id / requirement
id it came from — a generated CV with an empty `source_trace` is a bug, and
`render` never runs at all when the gate is blocked."""

from decimal import Decimal
from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from app.data.files.storage import get_storage
from app.data.repositories import document_repo
from app.models.application import Application
from app.models.document import ApplicationDocument, GeneratedDocument, GeneratedDocumentType
from app.models.profile import TestStatus
from app.tools._json_utils import ExtractionParseError, extract_json_object
from app.tools.ground_check import verify_claim_grounded

__all__ = ["CvGenState", "CvFormat", "run_cv_gen"]


class CvFormat(StrEnum):
    STANDARD = "standard"
    EUROPASS = "europass"


class AllowedFact(BaseModel):
    """One fact the draft may cite, tagged with exactly where it came from —
    the tag is what `render` writes into `source_trace`, never something the
    draft LLM gets to assert about itself."""

    text: str
    source: str


class CvDraftClaim(BaseModel):
    section: str = "other"
    text: str


class CvDraft(BaseModel):
    claims: list[CvDraftClaim] = Field(default_factory=list)


class CvDraftError(Exception):
    """Raised when the LLM fails to produce schema-valid draft JSON after the
    reject-and-retry budget is exhausted (mirrors
    `extract_document_fields.DocumentFieldExtractionError`)."""


_DRAFT_SYSTEM_INSTRUCTION = """You are drafting a scholarship applicant's CV. You may state ONLY facts
explicitly given to you in the ALLOWED FACTS list below — never invent, estimate, embellish, or infer
any additional detail (no invented employers, dates, achievements, publications, grades, or
certificates). If a typical CV section (e.g. work experience, publications) has no corresponding
allowed fact, OMIT that section entirely rather than inventing placeholder content.

Respond with ONLY a JSON object (no markdown, no commentary) matching exactly this shape:
{
  "claims": [
    {"section": one of "summary", "education", "experience", "skills", "certifications", "other",
     "text": string}
  ]
}
Each "text" MUST be a single, self-contained factual statement built only from the allowed facts."""

_DEFAULT_MAX_DRAFT_ATTEMPTS = 2


def _draft_cv(facts: list[AllowedFact], cv_format: str, scholarship_name: str, *, max_attempts: int) -> CvDraft:
    from app.core.llm import generate_text

    facts_block = "\n".join(f"- {fact.text}" for fact in facts)
    prompt = (
        f"Target CV format: {cv_format}\n"
        f"Scholarship: {scholarship_name or 'unspecified'}\n\n"
        f"ALLOWED FACTS (the only facts you may state):\n{facts_block}"
    )

    last_error: Exception | None = None
    for _ in range(max_attempts):
        raw = generate_text(prompt, system_instruction=_DRAFT_SYSTEM_INSTRUCTION)
        try:
            parsed = extract_json_object(raw)
            return CvDraft(**parsed)
        except (ExtractionParseError, ValidationError) as exc:
            last_error = exc
            continue

    raise CvDraftError(f"Could not draft valid CV content after {max_attempts} attempt(s): {last_error}")


class CvGenState(TypedDict, total=False):
    db: Any
    application: Application
    profile: Any
    documents: list[ApplicationDocument]
    scholarship: Any
    facts: list[AllowedFact]
    format: str
    draft: CvDraft | None
    source_trace: list[dict]
    gaps: list[dict]
    blocked: bool
    generated_document: GeneratedDocument | None
    rendered_text: str | None


def _fmt_decimal(value: Any) -> str:
    """Numeric facts (GPA, GPA scale) round-trip through Postgres `NUMERIC`
    as `Decimal`, which can drop a whole number's trailing `.0` (`4` instead
    of `4.0`). Left as-is, a fact ending "...GPA 3.8/4." reads exactly like a
    truncated "4.0" — the draft LLM reliably "corrects" it to "4.0" on its
    own, and `verify_claim_grounded`'s numeric-literal check (T097) then
    rejects that claim as an unsupported overstatement, purely on formatting.
    Always rendering one decimal place removes that whole failure mode
    without changing the actual value."""
    d = Decimal(str(value))
    if d == d.to_integral_value():
        d = d.quantize(Decimal("1.0"))
    return str(d)


def _profile_facts(profile: Any) -> list[AllowedFact]:
    if profile is None:
        return []
    facts: list[AllowedFact] = []

    if profile.name:
        facts.append(AllowedFact(text=f"Name: {profile.name}.", source="profile.name"))
    if profile.nationality:
        facts.append(AllowedFact(text=f"Nationality: {profile.nationality}.", source="profile.nationality"))

    for er in profile.education_records:
        parts = [p for p in (er.degree, f"in {er.field}" if er.field else None, f"from {er.university}" if er.university else None) if p]
        if er.gpa is not None:
            scale = f"/{_fmt_decimal(er.gpa_scale)}" if er.gpa_scale else ""
            parts.append(f"with GPA {_fmt_decimal(er.gpa)}{scale}")
        if not parts:
            continue
        span = f" ({er.start}–{er.end})" if er.start or er.end else ""
        facts.append(AllowedFact(text=f"Education: {' '.join(parts)}{span}.", source=f"profile.education_records:{er.id}"))

    for ts in profile.test_scores:
        if ts.status != TestStatus.HAVE or ts.score is None:
            continue
        test_type = ts.test_type.value if hasattr(ts.test_type, "value") else ts.test_type
        facts.append(AllowedFact(text=f"{test_type} score: {ts.score}.", source=f"profile.test_scores:{ts.id}"))

    for exp in profile.experience:
        title_org = " at ".join(p for p in (exp.title, exp.org) if p)
        detail = exp.detail or ""
        text = " ".join(p for p in (title_org, detail) if p).strip()
        if not text:
            continue
        kind = exp.kind.value if hasattr(exp.kind, "value") else exp.kind
        span = f" ({exp.start}–{exp.end})" if exp.start or exp.end else ""
        facts.append(AllowedFact(text=f"{str(kind).title()}: {text}{span}.", source=f"profile.experience:{exp.id}"))

    return facts


def _document_facts(documents: list[ApplicationDocument]) -> list[AllowedFact]:
    facts: list[AllowedFact] = []
    for doc in documents:
        meta = doc.parsed_meta or {}
        if meta.get("gpa") is not None:
            scale = f"/{meta['gpa_scale']}" if meta.get("gpa_scale") else ""
            facts.append(AllowedFact(text=f"Document-verified GPA: {meta['gpa']}{scale}.", source=f"document:{doc.id}"))
        if meta.get("degree"):
            institution = f" from {meta['institution']}" if meta.get("institution") else ""
            facts.append(AllowedFact(text=f"Document-verified degree: {meta['degree']}{institution}.", source=f"document:{doc.id}"))
        if meta.get("graduation_date"):
            facts.append(AllowedFact(text=f"Document-verified graduation date: {meta['graduation_date']}.", source=f"document:{doc.id}"))
        for score in meta.get("test_scores") or []:
            if score.get("score") is None:
                continue
            facts.append(
                AllowedFact(
                    text=f"Document-verified {score.get('test_type')} score: {score['score']}.",
                    source=f"document:{doc.id}",
                )
            )
    return facts


def _requirement_facts(scholarship: Any) -> list[AllowedFact]:
    if scholarship is None:
        return []
    facts: list[AllowedFact] = []
    for req in getattr(scholarship, "requirements", None) or []:
        if req.value_status != "known" or req.value is None:
            continue
        facts.append(AllowedFact(text=f"Scholarship requirement ({req.category}): {req.key} = {req.value}.", source=f"requirement:{req.id}"))
    return facts


def _gather_node(state: CvGenState) -> dict:
    facts = _profile_facts(state.get("profile")) + _document_facts(state.get("documents") or []) + _requirement_facts(state.get("scholarship"))
    return {"facts": facts}


def _select_format(scholarship: Any) -> CvFormat:
    """Requirement-aware format selection (BP §16): an explicit `cv_format`
    requirement or scholarship field drives the choice; otherwise the
    sensible default. Never assumed from anything else."""
    if scholarship is None:
        return CvFormat.STANDARD

    for req in getattr(scholarship, "requirements", None) or []:
        if req.value_status != "known" or req.value is None:
            continue
        haystack = f"{req.key} {req.value}".lower()
        if "europass" in haystack:
            return CvFormat.EUROPASS

    for field in getattr(scholarship, "fields", None) or []:
        if field.value_status != "known" or field.value is None:
            continue
        haystack = f"{field.key} {field.value}".lower()
        if "europass" in haystack:
            return CvFormat.EUROPASS

    return CvFormat.STANDARD


def _select_format_node(state: CvGenState) -> dict:
    return {"format": _select_format(state.get("scholarship")).value}


def _draft_node(state: CvGenState) -> dict:
    facts = state.get("facts") or []
    if not facts:
        return {
            "draft": None,
            "gaps": [
                {
                    "claim": None,
                    "reason": "No profile, document, or verified-requirement information is available to draft a CV from.",
                }
            ],
        }

    scholarship = state.get("scholarship")
    scholarship_name = getattr(scholarship, "name", "") or ""
    try:
        draft = _draft_cv(facts, state.get("format", CvFormat.STANDARD.value), scholarship_name, max_attempts=_DEFAULT_MAX_DRAFT_ATTEMPTS)
    except CvDraftError as exc:
        return {"draft": None, "gaps": [{"claim": None, "reason": str(exc)}]}
    return {"draft": draft}


def _ground_check_node(state: CvGenState) -> dict:
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
                    "reason": "Not traceable to the profile, an uploaded document, or a verified scholarship requirement.",
                }
            )
            continue
        trace.append({"claim": claim.text, "grounded_in": source_by_evidence.get(result.matched_evidence, "unknown")})

    if gaps:
        return {"blocked": True, "gaps": gaps}
    return {"blocked": False, "source_trace": trace}


def _render_node(state: CvGenState) -> dict:
    if state.get("blocked"):
        return {}

    draft = state["draft"]
    application = state["application"]
    cv_format = state.get("format", CvFormat.STANDARD.value)

    sections: dict[str, list[str]] = {}
    for claim in draft.claims:
        sections.setdefault(claim.section, []).append(claim.text)

    lines = [f"CV ({cv_format})", ""]
    for section, texts in sections.items():
        lines.append(section.upper())
        lines.extend(f"- {text}" for text in texts)
        lines.append("")
    rendered_text = "\n".join(lines).strip() + "\n"

    storage = get_storage()
    file_ref = storage.save(application.user_id, "cv.txt", rendered_text.encode("utf-8"))

    document = document_repo.create_generated_document(
        state["db"],
        application.user_id,
        application.id,
        GeneratedDocumentType.CV,
        file_ref,
        source_trace=state.get("source_trace") or [],
    )
    return {"generated_document": document, "rendered_text": rendered_text}


def _build_graph():
    graph = StateGraph(CvGenState)
    graph.add_node("gather", _gather_node)
    graph.add_node("select_format", _select_format_node)
    graph.add_node("draft", _draft_node)
    graph.add_node("ground_check", _ground_check_node)
    graph.add_node("render", _render_node)

    graph.add_edge(START, "gather")
    graph.add_edge("gather", "select_format")
    graph.add_edge("select_format", "draft")
    graph.add_edge("draft", "ground_check")
    graph.add_edge("ground_check", "render")
    graph.add_edge("render", END)
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_cv_gen(
    db,
    *,
    application: Application,
    profile: Any,
    documents: list[ApplicationDocument],
    scholarship: Any,
) -> CvGenState:
    initial_state: CvGenState = {
        "db": db,
        "application": application,
        "profile": profile,
        "documents": documents,
        "scholarship": scholarship,
    }
    return _COMPILED_GRAPH.invoke(initial_state)
