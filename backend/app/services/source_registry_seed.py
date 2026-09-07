"""Idempotent Source Registry seed loader (Blueprint §29).

Reads `app/data/seeds/seed_sources.yaml`, validates each row through
`SourceRegistryCreate`, and upserts on `domain` (update-if-exists,
insert-if-new) via `source_repo.upsert_source_by_domain` — never deletes, so
a source approved later through the candidate-source flow is left
untouched.

CLI: `uv run python -m app.services.source_registry_seed`
"""

from pathlib import Path

import yaml

from app.data.repositories.db import SessionLocal
from app.data.repositories.source_repo import upsert_source_by_domain
from app.schemas.source import SourceRegistryCreate, SourceRegistryRead

SEED_FILE = Path(__file__).resolve().parents[1] / "data" / "seeds" / "seed_sources.yaml"


def load_seed_rows(path: Path = SEED_FILE) -> list[SourceRegistryCreate]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [SourceRegistryCreate.model_validate(row) for row in raw]


def seed_sources(path: Path = SEED_FILE) -> list[SourceRegistryRead]:
    """Returns detached-safe `SourceRegistryRead` snapshots (converted while
    the session is still open) rather than ORM instances, since the caller
    may inspect them after this function's session has closed."""

    rows = load_seed_rows(path)
    with SessionLocal() as db:
        sources = [upsert_source_by_domain(db, row) for row in rows]
        return [SourceRegistryRead.model_validate(source) for source in sources]


def main() -> None:
    sources = seed_sources()
    for source in sources:
        print(f"seeded: {source.domain} ({source.status})")
    print(f"{len(sources)} source(s) seeded from {SEED_FILE}")


if __name__ == "__main__":
    main()
