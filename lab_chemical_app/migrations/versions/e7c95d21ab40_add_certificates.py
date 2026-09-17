"""add certificates and the pipes each one covers

The customer-facing forms (QC-01-F-28 warranty, QC-01-F-26 work test, and the
MTC) go out under the company's name, so the certificate number has to be stable
across reprints — which means storing the certificate rather than regenerating
it. The pipes a certificate covers are a chosen subset of the order, not the
whole order, so they get their own join table.

All three forms share this table and are told apart by `cert_type`: they differ
in what is printed below the material details, not in what is recorded.

Revision ID: e7c95d21ab40
Revises: d3a7f19c04b2
Create Date: 2026-09-02

"""
from alembic import op
import sqlalchemy as sa


revision = 'e7c95d21ab40'
down_revision = 'd3a7f19c04b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'certificates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('certificate_no', sa.String(length=30), nullable=False),
        sa.Column('cert_type', sa.String(length=20), nullable=False,
                  server_default='warranty'),
        sa.Column('production_order_id', sa.Integer(), nullable=False),
        sa.Column('issue_date', sa.Date(), nullable=False),
        sa.Column('warranty_start_date', sa.Date(), nullable=True),
        sa.Column('warranty_years', sa.Integer(), nullable=False,
                  server_default='3'),
        sa.Column('values_mode', sa.String(length=20), nullable=False,
                  server_default='standard'),
        sa.Column('customer_order_no', sa.String(length=100), nullable=True),
        sa.Column('project_name', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['production_order_id'], ['production_orders.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    # `unique=True, index=True` on the model is one unique index, not an index
    # plus a separate constraint.
    op.create_index('ix_certificates_certificate_no', 'certificates',
                    ['certificate_no'], unique=True)
    op.create_index('ix_certificates_cert_type', 'certificates', ['cert_type'])
    op.create_index('ix_certificates_production_order_id', 'certificates',
                    ['production_order_id'])

    op.create_table(
        'certificate_pipes',
        sa.Column('certificate_id', sa.Integer(), nullable=False),
        sa.Column('pipe_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['certificate_id'], ['certificates.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['pipe_id'], ['pipes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('certificate_id', 'pipe_id'),
    )


def downgrade():
    op.drop_table('certificate_pipes')
    op.drop_index('ix_certificates_production_order_id', table_name='certificates')
    op.drop_index('ix_certificates_cert_type', table_name='certificates')
    op.drop_index('ix_certificates_certificate_no', table_name='certificates')
    op.drop_table('certificates')
