"""add chat_messages.tool_calls and agent permissions

Revision ID: be1b340cd649
Revises: b1f4a2c73d90
Create Date: 2026-08-30 00:20:38.921531

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'be1b340cd649'
down_revision = 'b1f4a2c73d90'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tool_calls', sa.JSON(), nullable=True))

    # Seed new permission rows for agent features.
    # chatbot.agent → every role that already has chatbot.view
    # chatbot.sql  → super_admin and admin only
    conn = op.get_bind()
    perms = sa.table('permissions', sa.column('id', sa.Integer),
                     sa.column('module', sa.String), sa.column('screen', sa.String),
                     sa.column('action', sa.String), sa.column('description', sa.String))
    role_perms = sa.table('role_permissions', sa.column('id', sa.Integer),
                          sa.column('role', sa.String), sa.column('permission_id', sa.Integer))

    # Insert the two new permission rows if they don't exist
    for screen, action, desc in [
        ('agent', 'execute', 'Use the AI agent with tool calling'),
        ('sql', 'execute', 'Run raw SQL queries via the agent'),
    ]:
        exists = conn.execute(
            sa.select(perms.c.id).where(
                perms.c.module == 'chatbot', perms.c.screen == screen
            )
        ).first()
        if exists:
            continue
        conn.execute(perms.insert().values(
            module='chatbot', screen=screen, action=action, description=desc
        ))

    # Grant chatbot.agent to every role that has chatbot.view
    agent_perm = conn.execute(
        sa.select(perms.c.id).where(
            perms.c.module == 'chatbot', perms.c.screen == 'agent'
        )
    ).first()
    if agent_perm:
        agent_pid = agent_perm[0]
        view_perm = conn.execute(
            sa.select(perms.c.id).where(
                perms.c.module == 'chatbot', perms.c.screen == 'view'
            )
        ).first()
        if view_perm:
            view_pid = view_perm[0]
            roles_with_view = conn.execute(
                sa.select(role_perms.c.role).where(role_perms.c.permission_id == view_pid)
            ).fetchall()
            for (role,) in roles_with_view:
                exists = conn.execute(
                    sa.select(role_perms.c.id).where(
                        role_perms.c.role == role, role_perms.c.permission_id == agent_pid
                    )
                ).first()
                if not exists:
                    conn.execute(role_perms.insert().values(role=role, permission_id=agent_pid))

    # Grant chatbot.sql to super_admin and admin only
    sql_perm = conn.execute(
        sa.select(perms.c.id).where(
            perms.c.module == 'chatbot', perms.c.screen == 'sql'
        )
    ).first()
    if sql_perm:
        sql_pid = sql_perm[0]
        for role in ('super_admin', 'admin'):
            exists = conn.execute(
                sa.select(role_perms.c.id).where(
                    role_perms.c.role == role, role_perms.c.permission_id == sql_pid
                )
            ).first()
            if not exists:
                conn.execute(role_perms.insert().values(role=role, permission_id=sql_pid))


def downgrade():
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.drop_column('tool_calls')
