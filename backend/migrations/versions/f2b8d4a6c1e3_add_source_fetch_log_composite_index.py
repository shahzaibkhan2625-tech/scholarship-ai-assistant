"""add source_fetch_log composite index

Revision ID: f2b8d4a6c1e3
Revises: e81b5c40d9a2
Create Date: 2026-10-13 14:00:00.000000

002 US8 B2 migration (c), hand-written, additive and index-only. Serves the
newest-first per-source reads on `source_fetch_log` (coverage `DISTINCT ON`,
recent-log lists, the retention floors). The older single-column
`ix_source_fetch_log_source_id` is left in place (now redundant; a later cleanup
may drop it). Plain CREATE INDEX: the table is small. Apply on Neon's DIRECT
(non-pooled) endpoint. The application is correct without it, only slower.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2b8d4a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'e81b5c40d9a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        op.f('ix_source_fetch_log_source_started'),
        'source_fetch_log',
        ['source_id', sa.text('started_at DESC'), sa.text('id DESC')],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_source_fetch_log_source_started'), table_name='source_fetch_log')
