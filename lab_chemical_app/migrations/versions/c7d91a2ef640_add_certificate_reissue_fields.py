"""add certificate reissue fields, pipe-number option, and ordinary claims

Revision ID: c7d91a2ef640
Revises: a9d2e7f14c58
Create Date: 2026-09-16

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d91a2ef640'
down_revision = 'a9d2e7f14c58'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    certificate_columns = {
        column['name'] for column in inspector.get_columns('certificates')
    }
    with op.batch_alter_table('certificates') as batch:
        if 'show_pipe_numbers' not in certificate_columns:
            batch.add_column(sa.Column(
                'show_pipe_numbers', sa.Boolean(), nullable=False,
                server_default=sa.false()))
        if 'reissue_of_id' not in certificate_columns:
            batch.add_column(sa.Column(
                'reissue_of_id', sa.Integer(), nullable=True))
        if 'reissue_reason' not in certificate_columns:
            batch.add_column(sa.Column('reissue_reason', sa.Text(), nullable=True))

    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys('certificates')
    has_reissue_fk = any(
        set(foreign_key.get('constrained_columns') or []) == {'reissue_of_id'}
        and foreign_key.get('referred_table') == 'certificates'
        for foreign_key in foreign_keys
    )
    indexes = {index['name'] for index in inspector.get_indexes('certificates')}
    with op.batch_alter_table('certificates') as batch:
        if not has_reissue_fk:
            batch.create_foreign_key(
                'fk_certificates_reissue_of_id_certificates',
                'certificates', ['reissue_of_id'], ['id'], ondelete='RESTRICT')
        if 'ix_certificates_reissue_of_id' not in indexes:
            batch.create_index(
                'ix_certificates_reissue_of_id', ['reissue_of_id'], unique=False)

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'certificate_pipe_claims' not in tables:
        op.create_table(
            'certificate_pipe_claims',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('certificate_id', sa.Integer(), nullable=False),
            sa.Column('pipe_id', sa.Integer(), nullable=False),
            sa.Column('cert_type', sa.String(length=20), nullable=False),
            sa.ForeignKeyConstraint(
                ['certificate_id'], ['certificates.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['pipe_id'], ['pipes.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint(
                'pipe_id', 'cert_type',
                name='uq_certificate_pipe_claims_pipe_type'),
            sa.UniqueConstraint(
                'certificate_id', 'pipe_id',
                name='uq_certificate_pipe_claims_certificate_pipe'),
        )
        op.create_index(
            'ix_certificate_pipe_claims_certificate_id',
            'certificate_pipe_claims', ['certificate_id'], unique=False)
        op.create_index(
            'ix_certificate_pipe_claims_pipe_id',
            'certificate_pipe_claims', ['pipe_id'], unique=False)
    # Existing reissues deliberately do not own the ordinary claim. If legacy
    # data already contains two ordinary certificates of the same type for one
    # pipe, the unique constraint makes the migration fail visibly rather than
    # silently choosing an arbitrary certificate.
    op.execute(sa.text("""
        INSERT INTO certificate_pipe_claims
            (certificate_id, pipe_id, cert_type)
        SELECT c.id, cp.pipe_id, c.cert_type
        FROM certificates AS c
        JOIN certificate_pipes AS cp ON cp.certificate_id = c.id
        WHERE c.reissue_of_id IS NULL
        ON CONFLICT (pipe_id, cert_type) DO NOTHING
        """))


def downgrade():
    op.drop_index(
        'ix_certificate_pipe_claims_pipe_id',
        table_name='certificate_pipe_claims')
    op.drop_index(
        'ix_certificate_pipe_claims_certificate_id',
        table_name='certificate_pipe_claims')
    op.drop_table('certificate_pipe_claims')
    with op.batch_alter_table('certificates') as batch:
        batch.drop_index('ix_certificates_reissue_of_id')
        batch.drop_constraint(
            'fk_certificates_reissue_of_id_certificates', type_='foreignkey')
        batch.drop_column('reissue_reason')
        batch.drop_column('reissue_of_id')
        batch.drop_column('show_pipe_numbers')
