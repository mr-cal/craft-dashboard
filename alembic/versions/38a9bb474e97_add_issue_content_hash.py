"""add issue content_hash

Revision ID: 38a9bb474e97
Revises: dc49f831c706
Create Date: 2026-07-22 21:29:11.583034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38a9bb474e97'
down_revision: Union[str, None] = 'dc49f831c706'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_issues_content_hash', 'issues', ['content_hash'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_issues_content_hash', table_name='issues')
    op.drop_column('issues', 'content_hash')
