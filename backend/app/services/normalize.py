"""`normalize` service (T079, Blueprint §31, §14) — shapes raw extracted
fields into the `scholarship_fields` contract: value + value_status +
confidence per field. A field absent (or empty/None) from the raw record is
stored as `value_status='unknown'` with `value=None` — never guessed or
defaulted to a plausible-looking value (constitution Principle I; PRD B3)."""

from dataclasses import dataclass
from typing import Any

__all__ = ["NormalizedField", "normalize_record"]

_VALID_VALUE_STATUSES = {"known", "unknown", "not_applicable", "conditional", "conflicting"}
_VALID_CONFIDENCES = {"verified", "inferred", "unknown"}


@dataclass(frozen=True)
class NormalizedField:
    key: str
    value: Any | None
    value_status: str  # known | unknown | not_applicable | conditional | conflicting
    confidence: str  # verified | inferred | unknown


def normalize_record(raw: dict[str, Any], *, keys: list[str] | None = None) -> list[NormalizedField]:
    """`raw` is a flat dict of `field -> value`. A field may additionally
    carry a pre-existing `f"{key}_status"` / `f"{key}_confidence"` entry
    (e.g. already annotated by `extract_requirements`'s LLM extraction) which
    is honored as-is; otherwise a present, non-empty value defaults to
    `known`/`inferred` and a missing/empty value is always `unknown`/
    `unknown` — never a guessed default.

    `keys` restricts which top-level fields are normalized (defaults to
    every key in `raw` that isn't itself a `_status`/`_confidence` sidecar).
    """

    candidate_keys = keys if keys is not None else [
        key for key in raw if not key.endswith("_status") and not key.endswith("_confidence")
    ]

    normalized: list[NormalizedField] = []
    for key in candidate_keys:
        value = raw.get(key)
        explicit_status = raw.get(f"{key}_status")
        explicit_confidence = raw.get(f"{key}_confidence")

        is_absent = value is None or value == "" or value == []
        if is_absent:
            normalized.append(
                NormalizedField(
                    key=key,
                    value=None,
                    value_status=explicit_status if explicit_status in _VALID_VALUE_STATUSES else "unknown",
                    confidence=explicit_confidence if explicit_confidence in _VALID_CONFIDENCES else "unknown",
                )
            )
            continue

        normalized.append(
            NormalizedField(
                key=key,
                value=value,
                value_status=explicit_status if explicit_status in _VALID_VALUE_STATUSES else "known",
                confidence=explicit_confidence if explicit_confidence in _VALID_CONFIDENCES else "inferred",
            )
        )

    return normalized
