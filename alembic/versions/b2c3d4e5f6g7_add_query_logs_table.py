"""add query_logs table

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'query_logs',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('rule_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('mcp_server', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''),
        sa.Column('sql', sa.Text(), nullable=False, server_default=''),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='success'),
        sa.Column('rows_returned', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('agent_iteration', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_query_logs_rule_id', 'query_logs', ['rule_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_query_logs_rule_id', table_name='query_logs')
    op.drop_table('query_logs')
