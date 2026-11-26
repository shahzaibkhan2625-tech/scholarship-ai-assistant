"""quota governance: budget ledger, pause state, check state, classify queue

Revision ID: b4e7a91c3d52
Revises: a807daf8460e
Create Date: 2026-11-27 09:00:00.000000

003 quota governance (ADR-0014), hand-written. Additive only: four new tables and three new columns,
nothing altered or dropped, no CHECK constraint, no GRANT. Apply as the owner role over the direct URL
BEFORE deploying 003 code, then re-run the role script (docs/operations-hardening.md sections 14 and
16) so the app role gets default privileges on the new tables. Safe to leave applied on rollback: the
code running before 003 ignores the new objects. `alembic downgrade -1` drops them and loses only
budget history, check state, the pending-classification queue and the `outcome` annotations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b4e7a91c3d52'
down_revision: Union[str, Sequence[str], None] = 'a807daf8460e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'llm_budget_ledger',
        sa.Column('budget_day', sa.Date(), nullable=False),
        sa.Column('request_type', sa.String(length=16), nullable=False),
        sa.Column('priority_class', sa.String(length=24), nullable=False),
        sa.Column('event', sa.String(length=24), nullable=False),
        sa.Column('count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('budget_day', 'request_type', 'priority_class', 'event'),
    )
    op.create_table(
        'llm_budget_state',
        sa.Column('request_type', sa.String(length=16), nullable=False),
        sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pause_reason', sa.String(length=64), nullable=True),
        sa.Column('pause_set_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('observed_limit', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('request_type'),
    )
    op.create_table(
        'source_check_state',
        sa.Column('source_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=True),
        sa.Column('fingerprint_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('etag', sa.Text(), nullable=True),
        sa.Column('last_modified', sa.Text(), nullable=True),
        sa.Column('validators_trusted', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('last_extraction_outcome', sa.String(length=24), nullable=True),
        sa.Column('last_full_extraction_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('backoff_level', sa.Integer(), server_default='0', nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checks_window', sa.Integer(), server_default='0', nullable=False),
        sa.Column('changed_window', sa.Integer(), server_default='0', nullable=False),
        sa.Column('rules_review_needed', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('rules_review_reason', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['source_id'], ['source_registry.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('source_id'),
    )
    op.create_table(
        'classify_pending',
        sa.Column('scholarship_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_reason', sa.String(length=32), nullable=True),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['scholarship_id'], ['scholarships.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('scholarship_id'),
    )
    op.add_column('source_fetch_log', sa.Column('outcome', sa.String(length=24), nullable=True))
    op.add_column('monitoring_runs', sa.Column('sources_skipped', sa.Integer(), server_default='0', nullable=False))
    op.add_column('monitoring_runs', sa.Column('sources_deferred', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('monitoring_runs', 'sources_deferred')
    op.drop_column('monitoring_runs', 'sources_skipped')
    op.drop_column('source_fetch_log', 'outcome')
    op.drop_table('classify_pending')
    op.drop_table('source_check_state')
    op.drop_table('llm_budget_state')
    op.drop_table('llm_budget_ledger')
