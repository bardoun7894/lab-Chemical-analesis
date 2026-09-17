"""add certificate_orders, so one certificate can cover several orders

A delivery does not always draw on a single production order — a customer's
truck can carry pipes made under two order numbers. `certificates.production_order_id`
stays NOT NULL and keeps meaning "the primary order" (print context, the list
page, and certificate_service all still read it), but the actual coverage now
lives in this join table, one row per order the certificate touches.

Certificates issued before this migration get no rows here at all; the model's
`Certificate.orders` falls back to `[production_order]` for those, so nothing
already printed or listed needs a backfill.

Revision ID: f4a8c31b96de
Revises: e7c95d21ab40
Create Date: 2026-09-05

"""
from alembic import op
import sqlalchemy as sa


revision = 'f4a8c31b96de'
down_revision = 'e7c95d21ab40'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'certificate_orders',
        sa.Column('certificate_id', sa.Integer(), nullable=False),
        sa.Column('production_order_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['certificate_id'], ['certificates.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['production_order_id'], ['production_orders.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('certificate_id', 'production_order_id'),
    )


def downgrade():
    op.drop_table('certificate_orders')
