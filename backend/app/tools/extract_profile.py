"""`extract_profile` tool — CV-import field suggestions (LLM-assisted). Per
the FR contract these are suggestions only: the caller (API layer) never
auto-saves them, the user must confirm before they hit the profile."""

from app.schemas.profile import ProfileUpdateRequest
from app.tools._json_utils import extract_json_object

_SYSTEM_INSTRUCTION = (
    "You extract structured profile fields from a student's CV/resume text. "
    "Respond with ONLY a JSON object (no markdown, no commentary) with exactly "
    "these keys: name, nationality, country_of_residence, current_degree, "
    "target_degree_level (one of BS, MS, PhD, other, or null), target_fields "
    "(array of strings), target_countries (array of strings). Use null for any "
    "field you cannot confidently determine from the text — never guess."
)


def extract_profile_from_text(cv_text: str) -> ProfileUpdateRequest:
    from app.core.llm import generate_text

    raw = generate_text(cv_text, system_instruction=_SYSTEM_INSTRUCTION)
    parsed = extract_json_object(raw)
    return ProfileUpdateRequest(**{k: v for k, v in parsed.items() if k in ProfileUpdateRequest.model_fields})
