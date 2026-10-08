"""add created_by to tickets

Revision ID: b3f1d2a7c9e4
Revises: 94a58e11a945
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f1d2a7c9e4'
down_revision: Union[str, Sequence[str], None] = '94a58e11a945'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('tickets', sa.Column('created_by', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_tickets_created_by_users',
        'tickets', 'users',
        ['created_by'], ['id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_tickets_created_by_users', 'tickets', type_='foreignkey')
    op.drop_column('tickets', 'created_by')
