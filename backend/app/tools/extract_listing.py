"""`extract_listing` tool (T135, Blueprint §11; Resolution note 5) — LLM-assisted
extraction of MULTIPLE candidate scholarships from an already-fetched listing/index
page (a source's `list_page_url`), as opposed to `extract_requirements.py`'s single
already-identified scholarship page. This tool only extracts; it never decides
eligibility, and it never decides which domains are authoritative — Principle IV is
already enforced upstream, at the source-registry gate the caller passed through
before this tool ever sees any content (see `agents/discovery/agent.py`).

**Grounding, not trust-the-LLM (constitution "harness discipline"):** every candidate
name the LLM returns is checked, deterministically and without a second LLM call, via
`app.tools.ground_check.ground_check` against the same cleaned page text the LLM was
given. A scholarship name has no legitimate reason to be paraphrased — the system
instruction demands the LLM copy it verbatim — so the grounding threshold here is
`1.0` (every significant word of the name must appear in the source text), stricter
than `ground_check`'s `0.6` CV/SOP-paraphrase-tolerant default: that default exists for
claims that legitimately summarize a longer fact, which a scholarship's name is not.
A candidate that fails this check is dropped from `candidates` and counted in
`rejected_count`/`rejected_names` — never silently discarded with no trace.

Confidence discipline: this tool's output must reach `run_ingestion` with
`official_source_confirmed=False`, always — that flag is reserved for a page already
identified as THIS scholarship's own official page (`extract_requirements`'s use case);
a listing/index page is one step further removed. Enforcing that flag is the caller's
responsibility (`agents/discovery/agent.py`), not this tool's.

**No pre-filter tries to detect "this doesn't look like a real listing."** A live-fetch
investigation across the 5 seeded sources (T135 Phase A) found that a JS-rendered/
consent-gated shell page (e.g. KAUST) is not reliably distinguishable from a real
listing by length alone — it can still return several KB of unrelated nav/footer text.
That case is handled correctly anyway: the system instruction tells the LLM to return
an empty `candidates` list when the text names no scholarships, and the caller treats
`accepted_count == 0` as a signal worth surfacing (a coverage gap), never as a silent
"no scholarships" success. The only pre-filter here (`_MIN_VIABLE_TEXT_LENGTH`) guards
against literally blank/near-empty fetched content — e.g. `content=""` — so a trivial
empty-body case doesn't spend an LLM call for nothing.
"""

import re

from pydantic import BaseModel, Field

from app.tools._json_utils import extract_json_object
from app.tools.ground_check import ground_check

__all__ = ["ExtractedListingCandidate", "ExtractListingOutput", "clean_html_to_text", "extract_listing_from_page"]

_SYSTEM_INSTRUCTION = """You extract a LIST of scholarship/program candidates from a
scholarship-LISTING (index) page's text. This is a listing page with MULTIPLE
scholarships, not a single scholarship's own detail page.
Respond with ONLY a JSON object (no markdown, no commentary) matching exactly this shape:
{
  "candidates": [
    {
      "name": string,
      "provider": string or null,
      "country": string or null,
      "field": string or null,
      "degree_level": one of "BS", "MS", "PhD", "other", or null,
      "funding_status": one of "fully_funded", "substantially_funded", "partially_funded",
                        "tuition_only", "stipend_only", "other_combination", "unknown",
      "detail_url": string or null,
      "deadline": an ISO date string (YYYY-MM-DD) or null
    }
  ]
}
"name" MUST be copied EXACTLY as it appears in the source text — do not paraphrase,
translate, summarize, or reformat it; a name that doesn't appear verbatim will be
rejected. If the page text does not name any scholarships, return {"candidates": []} —
never invent a scholarship that isn't actually named in the text."""


class ExtractedListingCandidate(BaseModel):
    name: str
    provider: str | None = None
    country: str | None = None
    field: str | None = None
    degree_level: str | None = None
    funding_status: str = "unknown"
    detail_url: str | None = None
    deadline: str | None = None


class ExtractListingOutput(BaseModel):
    candidates: list[ExtractedListingCandidate] = Field(default_factory=list)
    accepted_count: int
    rejected_count: int
    rejected_names: list[str] = Field(default_factory=list)


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# Guards only against literally blank/near-empty fetched content (see module
# docstring) — not a "looks like a real listing" detector.
_MIN_VIABLE_TEXT_LENGTH = 100


def clean_html_to_text(html: str) -> str:
    """Strips script/style blocks and tags, collapsing whitespace — the same
    "give the LLM cleaned text, not raw markup" shape `extract_requirements.py`
    expects its caller to already have done, just performed here instead of
    assumed."""
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", html)
    without_tags = _TAG_RE.sub(" ", without_scripts)
    return _WHITESPACE_RE.sub(" ", without_tags).strip()


def _llm_extract_listing(page_text: str, *, source_url: str) -> dict:
    from app.core.llm import generate_text

    prompt = f"Scholarship listing page (source: {source_url}):\n\n{page_text[:20000]}"
    # temperature=0: this task is a literal-copy task, not a creative one --
    # "name" must reach the deterministic ground_check(threshold=1.0) below
    # verbatim, and default sampling temperature was observed (T135 Slice 3)
    # to occasionally reword a real name into an equally plausible paraphrase
    # that then correctly fails grounding. Lower, not zero-risk, temperature
    # reduces that without weakening the grounding check itself.
    raw = generate_text(prompt, system_instruction=_SYSTEM_INSTRUCTION, temperature=0.0)
    return extract_json_object(raw)


def extract_listing_from_page(html: str, *, source_url: str) -> ExtractListingOutput:
    page_text = clean_html_to_text(html or "")

    if len(page_text) < _MIN_VIABLE_TEXT_LENGTH:
        return ExtractListingOutput(candidates=[], accepted_count=0, rejected_count=0)

    parsed = _llm_extract_listing(page_text, source_url=source_url)
    raw_candidates = parsed.get("candidates") or []

    accepted: list[ExtractedListingCandidate] = []
    rejected_names: list[str] = []
    for raw_candidate in raw_candidates:
        try:
            candidate = ExtractedListingCandidate(**raw_candidate)
        except (TypeError, ValueError):
            continue  # malformed shape from the LLM — not a candidate at all, not a "rejected name" either
        if not candidate.name:
            continue
        check = ground_check(candidate.name, [page_text], threshold=1.0)
        if check.grounded:
            accepted.append(candidate)
        else:
            rejected_names.append(candidate.name)

    return ExtractListingOutput(
        candidates=accepted,
        accepted_count=len(accepted),
        rejected_count=len(rejected_names),
        rejected_names=rejected_names,
    )
