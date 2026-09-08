"""`conflict_resolution` service (T083, Blueprint §32) — when two or more
`scholarship_sources` rows disagree on a field's value: prefer **official**
over third-party, then **more-recent** over older, then **higher-reliability**
over lower. If none of those breaks the tie, the field is marked
`value_status='conflicting'` and BOTH (all) source values are retained in the
returned result — this module never silently picks a winner when the policy
genuinely can't distinguish the candidates.
"""

from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["SourceValue", "ConflictResolution", "resolve_conflict"]

_RELIABILITY_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class SourceValue:
    source_id: str
    value: object
    is_official: bool
    retrieved_at: datetime
    reliability: str = "medium"  # high | medium | low


@dataclass(frozen=True)
class ConflictResolution:
    value: object | None
    value_status: str  # known | conflicting
    resolved_by: str | None  # "official" | "recency" | "reliability" | None
    winning_source_id: str | None
    conflicting_values: list[SourceValue] = field(default_factory=list)


def resolve_conflict(candidates: list[SourceValue]) -> ConflictResolution:
    if not candidates:
        raise ValueError("resolve_conflict requires at least one candidate value")

    distinct_values = {repr(c.value) for c in candidates}
    if len(distinct_values) == 1:
        winner = candidates[0]
        return ConflictResolution(
            value=winner.value, value_status="known", resolved_by=None, winning_source_id=winner.source_id
        )

    official = [c for c in candidates if c.is_official]
    if len(official) == 1:
        winner = official[0]
        return ConflictResolution(
            value=winner.value,
            value_status="known",
            resolved_by="official",
            winning_source_id=winner.source_id,
            conflicting_values=list(candidates),
        )

    pool = official if len(official) > 1 else candidates

    if len(pool) > 1:
        latest_time = max(c.retrieved_at for c in pool)
        most_recent = [c for c in pool if c.retrieved_at == latest_time]
        if len(most_recent) == 1:
            winner = most_recent[0]
            return ConflictResolution(
                value=winner.value,
                value_status="known",
                resolved_by="recency",
                winning_source_id=winner.source_id,
                conflicting_values=list(candidates),
            )
        pool = most_recent

    if len(pool) > 1:
        best_rank = max(_RELIABILITY_RANK.get(c.reliability, 0) for c in pool)
        most_reliable = [c for c in pool if _RELIABILITY_RANK.get(c.reliability, 0) == best_rank]
        if len(most_reliable) == 1:
            winner = most_reliable[0]
            return ConflictResolution(
                value=winner.value,
                value_status="known",
                resolved_by="reliability",
                winning_source_id=winner.source_id,
                conflicting_values=list(candidates),
            )

    # Still tied on official-ness, recency, and reliability: never guess.
    return ConflictResolution(
        value=None,
        value_status="conflicting",
        resolved_by=None,
        winning_source_id=None,
        conflicting_values=list(candidates),
    )
