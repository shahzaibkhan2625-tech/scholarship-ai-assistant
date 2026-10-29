"""security hardening: operator flag, case-insensitive email, audit log

Revision ID: a807daf8460e
Revises: f2b8d4a6c1e3
Create Date: 2026-10-29 12:00:00.000000

010 migration, hand-written and additive (specs/010-security-hardening/data-model.md):
  * users.is_operator boolean NOT NULL default false (nobody is made operator here).
  * unique index on lower(users.email). FAILS if existing rows collide case-insensitively,
    which is the deliberate hard stop of FR-CRED-5: the owner resolves duplicates first.
  * audit_log (id, user_id, action, entity, at) with no foreign key, indexed on (at) and
    (user_id, at).
  * append-only by privilege: UPDATE, DELETE, TRUNCATE are revoked from the application role
    `scholarship_app`, but only if that role exists (it does not on a fresh CI/local database).
Apply on Neon's DIRECT (non-pooled) endpoint as the owner role; the owner does this, not the worker.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a807daf8460e'
down_revision: Union[str, Sequence[str], None] = 'f2b8d4a6c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REVOKE_AUDIT_LOG_SQL = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scholarship_app') THEN
    REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM scholarship_app;
  END IF;
END $$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('is_operator', sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index('ix_users_email_lower', 'users', [sa.text('lower(email)')], unique=True)
    op.create_table(
        'audit_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('entity', sa.Text(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_log_at', 'audit_log', ['at'], unique=False)
    op.create_index('ix_audit_log_user_id_at', 'audit_log', ['user_id', 'at'], unique=False)
    op.execute(REVOKE_AUDIT_LOG_SQL)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_audit_log_user_id_at', table_name='audit_log')
    op.drop_index('ix_audit_log_at', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_index('ix_users_email_lower', table_name='users')
    op.drop_column('users', 'is_operator')
