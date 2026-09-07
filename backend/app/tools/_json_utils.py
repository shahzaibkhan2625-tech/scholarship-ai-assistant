"""Shared helper for LLM-assisted extraction tools: pull a JSON object out of
a raw model response that may be wrapped in markdown code fences or preceded
by prose, despite being asked to respond with JSON only."""

import json
import re

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class ExtractionParseError(ValueError):
    pass


def extract_json_object(raw_text: str) -> dict:
    fence_match = _CODE_FENCE_RE.search(raw_text)
    candidate = fence_match.group(1) if fence_match else raw_text

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    object_match = _JSON_OBJECT_RE.search(candidate)
    if object_match:
        try:
            return json.loads(object_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ExtractionParseError(f"Could not parse a JSON object from model response: {raw_text!r}")
