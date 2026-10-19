"""add alerts and candidate validations

Revision ID: e81b5c40d9a2
Revises: c3a9f1d27b64
Create Date: 2026-10-04 12:05:00.000000

002 migration (b), hand-written and strictly additive (data-model.md §5–7):
- `alerts` (user-owned; includes nullable `read_at`, no default — O-1);
- `alert_preferences` (PK = user_id; both channel flags default true);
- `candidate_source_validations` (append-only advisory history; no user_id;
  `official_signals` server_default '{}').
Requires `monitoring_runs` from revision c3a9f1d27b64.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e81b5c40d9a2'
down_revision: Union[str, Sequence[str], None] = 'c3a9f1d27b64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('alerts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('scholarship_id', sa.UUID(), nullable=False),
    sa.Column('match_id', sa.UUID(), nullable=True),
    sa.Column('monitoring_run_id', sa.UUID(), nullable=True),
    sa.Column('change_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('channels_requested', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('delivered_in_app_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_email_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('email_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ),
    sa.ForeignKeyConstraint(['monitoring_run_id'], ['monitoring_runs.id'], ),
    sa.ForeignKeyConstraint(['scholarship_id'], ['scholarships.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_alerts_user_id'), 'alerts', ['user_id'], unique=False)
    op.create_index(op.f('ix_alerts_scholarship_id'), 'alerts', ['scholarship_id'], unique=False)
    op.create_table('alert_preferences',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('in_app_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('email_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('candidate_source_validations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('candidate_source_id', sa.UUID(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('reachable', sa.Boolean(), nullable=False),
    sa.Column('reachable_detail', sa.Text(), nullable=True),
    sa.Column('extractable', sa.Boolean(), nullable=False),
    sa.Column('extractable_detail', sa.Text(), nullable=True),
    sa.Column('official_signals', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.ForeignKeyConstraint(['candidate_source_id'], ['candidate_sources.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_candidate_source_validations_candidate_source_id'), 'candidate_source_validations', ['candidate_source_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_candidate_source_validations_candidate_source_id'), table_name='candidate_source_validations')
    op.drop_table('candidate_source_validations')
    op.drop_table('alert_preferences')
    op.drop_index(op.f('ix_alerts_scholarship_id'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_user_id'), table_name='alerts')
    op.drop_table('alerts')
