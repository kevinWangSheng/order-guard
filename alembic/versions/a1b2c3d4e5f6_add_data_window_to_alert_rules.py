"""add data_window to alert_rules

Revision ID: a1b2c3d4e5f6
Revises: 0d3a775bcb95
Create Date: 2026-03-08 17:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '0d3a775bcb95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('data_window', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.drop_column('data_window')
