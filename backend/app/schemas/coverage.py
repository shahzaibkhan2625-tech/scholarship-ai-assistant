"""Coverage response schema (002 US4, contracts `CoverageSnapshot`).

A thin route-facing alias of `CoverageSummary` (schemas/discovery.py), so the
Discovery agent and `GET /coverage` share one shape and one `Literal[False]`
completeness pin (FR-COVAN-2)."""

from app.schemas.discovery import CoverageSummary


class CoverageSnapshot(CoverageSummary):
    pass
