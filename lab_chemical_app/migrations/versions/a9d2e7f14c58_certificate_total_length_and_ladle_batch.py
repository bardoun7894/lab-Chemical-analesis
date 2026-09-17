"""certificate total length override + ladle batch number

Two fields the client asked for on the same walkthrough (2026-09-05):

* ``certificates.total_length_m`` — the certificate's quantity in metres is
  computed from the ticked pipes' Finish measurements, but the client wants to
  confirm or correct that figure before it prints. NULL means "print what the
  pipes add up to"; a value is printed as-is on all three forms.

* ``chemical_analyses.batch_no`` — the batch number the client keeps per ladle
  on the chemical-analysis sheet. The MTC used to look for it on the annealing
  stage's bundle number, which is the wrong record and was empty for almost
  every heat.

Revision ID: a9d2e7f14c58
Revises: f4a8c31b96de
Create Date: 2026-09-05

"""
from alembic import op
import sqlalchemy as sa


revision = 'a9d2e7f14c58'
down_revision = 'f4a8c31b96de'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('certificates') as batch:
        batch.add_column(sa.Column('total_length_m', sa.Float(), nullable=True))
    with op.batch_alter_table('chemical_analyses') as batch:
        batch.add_column(sa.Column('batch_no', sa.String(length=50), nullable=True))
        batch.create_index('ix_chemical_analyses_batch_no', ['batch_no'])


def downgrade():
    with op.batch_alter_table('chemical_analyses') as batch:
        batch.drop_index('ix_chemical_analyses_batch_no')
        batch.drop_column('batch_no')
    with op.batch_alter_table('certificates') as batch:
        batch.drop_column('total_length_m')
