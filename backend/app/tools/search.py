"""`search` tool (Blueprint §11, §15) — approved-source-scoped search, never
unrestricted web search (FR-DISC-2, PRD B3 Hard-Gated Zone). Every raw hit is
checked against `source_registry` and dropped unless its domain belongs to a
row with `status == active`; the LLM cannot expand the result set beyond
governed sources by shaping its query.

No MCP search-server provider has been chosen yet (research.md line 55) —
`raw_search` is injectable so a real provider can be wired in later without
changing this contract. The default raises, so silently returning zero
results (which would look like "no scholarships found") is impossible.
"""

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.repositories import source_repo

__all__ = ["RawSearchHit", "SearchHit", "search_tool"]


@dataclass(frozen=True)
class RawSearchHit:
    title: str
    url: str
    snippet: str


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str
    source_id: str


def _raw_search_not_configured(query: str) -> list[RawSearchHit]:
    raise NotImplementedError(
        "No search backend is configured (research.md: MCP search-server "
        "provider not yet chosen). Pass raw_search= to use this tool."
    )


def search_tool(
    db: Session,
    query: str,
    *,
    raw_search: Callable[[str], list[RawSearchHit]] = _raw_search_not_configured,
) -> list[SearchHit]:
    domain_to_source_id = {source.domain: str(source.id) for source in source_repo.get_active_sources(db)}

    scoped: list[SearchHit] = []
    for hit in raw_search(query):
        source_id = domain_to_source_id.get(urlparse(hit.url).netloc)
        if source_id is None:
            continue
        scoped.append(SearchHit(title=hit.title, url=hit.url, snippet=hit.snippet, source_id=source_id))
    return scoped
