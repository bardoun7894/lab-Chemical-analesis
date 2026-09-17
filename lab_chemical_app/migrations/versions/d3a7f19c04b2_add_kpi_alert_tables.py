"""add kpi_alerts and kpi_alert_runs

A KPI target breach is computed live, but has to be stored for the three
things asked of it: a bell that counts unread ones, a WhatsApp message sent
once rather than on every page load, and a breach that stops shouting when the
day recovers. `kpi_alert_runs` is the throttle — the app has no scheduler, so
readers trigger the recalculation and the four gunicorn workers need a shared
record of when it last ran.

Revision ID: d3a7f19c04b2
Revises: be1b340cd649
Create Date: 2026-09-02

"""
from alembic import op
import sqlalchemy as sa


revision = 'd3a7f19c04b2'
down_revision = 'be1b340cd649'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'kpi_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('kpi_key', sa.String(length=50), nullable=False),
        sa.Column('scope', sa.String(length=20), nullable=False,
                  server_default='plant'),
        # Never NULL: a NULL never equals a NULL, so the unique constraint
        # below would stop deduplicating plant-wide alerts.
        sa.Column('scope_key', sa.String(length=100), nullable=False,
                  server_default=''),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('target', sa.Float(), nullable=True),
        sa.Column('unit', sa.String(length=20), nullable=True),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('read_by_id', sa.Integer(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['read_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day', 'kpi_key', 'scope', 'scope_key',
                            name='uq_kpi_alert_identity'),
    )
    op.create_index('ix_kpi_alerts_day', 'kpi_alerts', ['day'])

    op.create_table(
        'kpi_alert_runs',
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('ran_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('day'),
    )


def downgrade():
    op.drop_table('kpi_alert_runs')
    op.drop_index('ix_kpi_alerts_day', table_name='kpi_alerts')
    op.drop_table('kpi_alerts')
