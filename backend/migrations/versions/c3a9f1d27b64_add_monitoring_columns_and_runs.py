"""add monitoring columns and runs

Revision ID: c3a9f1d27b64
Revises: ad4826bd57e6
Create Date: 2026-10-04 12:00:00.000000

002 migration (a), hand-written and strictly additive (data-model.md §1, §2, §4):
- creates `monitoring_runs` (system-owned, no user_id) FIRST, because
- `source_fetch_log.monitoring_run_id` is an FK to it;
- adds nullable `source_registry.listing_page_url` / `freshness_window_days`;
- adds `source_fetch_log.fetched_url` (nullable), `used_homepage_fallback`
  (NOT NULL, server_default false — existing rows correctly read false) and
  `monitoring_run_id` (nullable FK, indexed).
Enum columns follow the 001 pattern: non-native, stored by member NAME, no CHECK.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = 'c3a9f1d27b64'
down_revision: Union[str, Sequence[str], None] = 'ad4826bd57e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('monitoring_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('trigger', sa.Enum('SCHEDULED', 'MANUAL', name='monitoring_trigger', native_enum=False), nullable=False),
    sa.Column('status', sa.Enum('RUNNING', 'COMPLETED', 'FAILED', name='monitoring_status', native_enum=False), nullable=False),
    sa.Column('sources_processed', sa.Integer(), nullable=False),
    sa.Column('sources_failed', sa.Integer(), nullable=False),
    sa.Column('changes_detected', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.add_column('source_registry', sa.Column('listing_page_url', sa.Text(), nullable=True))
    op.add_column('source_registry', sa.Column('freshness_window_days', sa.Integer(), nullable=True))
    op.add_column('source_fetch_log', sa.Column('fetched_url', sa.Text(), nullable=True))
    op.add_column('source_fetch_log', sa.Column('used_homepage_fallback', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('source_fetch_log', sa.Column('monitoring_run_id', sa.UUID(), sa.ForeignKey('monitoring_runs.id'), nullable=True))
    op.create_index(op.f('ix_source_fetch_log_monitoring_run_id'), 'source_fetch_log', ['monitoring_run_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_source_fetch_log_monitoring_run_id'), table_name='source_fetch_log')
    op.drop_column('source_fetch_log', 'monitoring_run_id')
    op.drop_column('source_fetch_log', 'used_homepage_fallback')
    op.drop_column('source_fetch_log', 'fetched_url')
    op.drop_column('source_registry', 'freshness_window_days')
    op.drop_column('source_registry', 'listing_page_url')
    op.drop_table('monitoring_runs')
