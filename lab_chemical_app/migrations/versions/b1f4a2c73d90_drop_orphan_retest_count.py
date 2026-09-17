"""drop the orphan mechanical_tests.retest_count column

The column was created by app/services/migration_service.py, the hand-written
ALTER TABLE list that ran at boot. No model ever declared it and nothing in the
app or templates read it, so it was invisible to the ORM while still sitting in
the table — every autogenerate run would propose dropping it, which is exactly
the kind of noise that gets a real change waved through by accident.

All 79 production rows held the default 0 at the time of this migration, so
nothing is lost. The matching entry was removed from PENDING_MIGRATIONS in the
same commit; without that the next boot would simply add the column back.

This is the first change that the old mechanism could not have made at all: it
could add a column and nothing else.

Revision ID: b1f4a2c73d90
Revises: c6323e9b5e93
Create Date: 2026-08-30

"""
from alembic import op
import sqlalchemy as sa


revision = 'b1f4a2c73d90'
down_revision = 'c6323e9b5e93'
branch_labels = None
depends_on = None


def _has_retest_count():
    return 'retest_count' in {
        c['name'] for c in sa.inspect(op.get_bind()).get_columns('mechanical_tests')
    }


def upgrade():
    # Only existing installs have this column. It was created by the boot-time
    # ALTER list, never by a model, so the Alembic baseline does not create it
    # and a database built from empty never has it — an unconditional drop
    # makes the chain unreplayable, which is what CI hit.
    if _has_retest_count():
        op.drop_column('mechanical_tests', 'retest_count')


def downgrade():
    if not _has_retest_count():
        op.add_column(
            'mechanical_tests',
            sa.Column('retest_count', sa.Integer(), server_default='0', nullable=True),
        )
