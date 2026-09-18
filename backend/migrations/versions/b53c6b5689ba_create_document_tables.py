"""create document tables

Revision ID: b53c6b5689ba
Revises: d501fadbb3f1
Create Date: 2026-09-19 01:45:15.866015

Creates the minimal `applications` table (Resolution note 3 — `tasks`/
`submission_approvals` are added in Phase 4/T118) plus `application_documents`
and `generated_documents` (Phase 3/US5, data-model.md §6-8), including the
nullable `inconsistency_flags`/`source_trace` jsonb columns data-model.md §6/§7
specify — both populated later by doc_pipeline (T105) and ground_check-gated
generation (T109/T110), NULL on insert here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b53c6b5689ba'
down_revision: Union[str, Sequence[str], None] = 'd501fadbb3f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('applications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('scholarship_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['scholarship_id'], ['scholarships.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_applications_scholarship_id'), 'applications', ['scholarship_id'], unique=False)
    op.create_index(op.f('ix_applications_user_id'), 'applications', ['user_id'], unique=False)
    op.create_table('application_documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Enum('UNCLASSIFIED', 'TRANSCRIPT', 'DEGREE_CERTIFICATE', 'CV', 'EUROPASS_CV', 'SOP', 'MOTIVATION_LETTER', 'PERSONAL_STATEMENT', 'STUDY_PLAN', 'RESEARCH_PROPOSAL', 'RECOMMENDATION_INFO', 'CERTIFICATE', 'LANGUAGE_TEST_DOC', 'GRE_GMAT_DOC', 'PORTFOLIO', 'PUBLICATION', 'WORK_EXPERIENCE_DOC', 'CHARACTER_CERTIFICATE', 'FINANCIAL_DOC', 'SCHOLARSHIP_SPECIFIC_FORM', 'UNIVERSITY_SPECIFIC_FORM', 'OTHER', name='application_document_type', native_enum=False), nullable=False),
    sa.Column('file_ref', sa.String(), nullable=False),
    sa.Column('parsed_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('satisfies_requirement_id', sa.UUID(), nullable=True),
    sa.Column('inconsistency_flags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('checksum', sa.String(), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.ForeignKeyConstraint(['satisfies_requirement_id'], ['requirements.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_application_documents_application_id'), 'application_documents', ['application_id'], unique=False)
    op.create_index(op.f('ix_application_documents_user_id'), 'application_documents', ['user_id'], unique=False)
    op.create_table('generated_documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Enum('CV', 'SOP', 'MOTIVATION', 'OTHER', name='generated_document_type', native_enum=False), nullable=False),
    sa.Column('file_ref', sa.String(), nullable=False),
    sa.Column('source_trace', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_generated_documents_application_id'), 'generated_documents', ['application_id'], unique=False)
    op.create_index(op.f('ix_generated_documents_user_id'), 'generated_documents', ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_generated_documents_user_id'), table_name='generated_documents')
    op.drop_index(op.f('ix_generated_documents_application_id'), table_name='generated_documents')
    op.drop_table('generated_documents')
    op.drop_index(op.f('ix_application_documents_user_id'), table_name='application_documents')
    op.drop_index(op.f('ix_application_documents_application_id'), table_name='application_documents')
    op.drop_table('application_documents')
    op.drop_index(op.f('ix_applications_user_id'), table_name='applications')
    op.drop_index(op.f('ix_applications_scholarship_id'), table_name='applications')
    op.drop_table('applications')
