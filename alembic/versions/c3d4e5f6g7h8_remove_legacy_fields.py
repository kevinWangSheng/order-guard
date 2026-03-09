"""remove legacy connector_id connector_type data_type from alert_rules

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-03-08 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6g7h8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.drop_column('connector_id')
        batch_op.drop_column('connector_type')
        batch_op.drop_column('data_type')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('alert_rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('connector_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('connector_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='legacy'))
        batch_op.add_column(sa.Column('data_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
