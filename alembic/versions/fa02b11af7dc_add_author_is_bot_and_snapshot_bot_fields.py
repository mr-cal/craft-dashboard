"""add_author_is_bot_and_snapshot_bot_fields

Revision ID: fa02b11af7dc
Revises: a3f9d2e1b5c8
Create Date: 2026-06-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'fa02b11af7dc'
down_revision: Union[str, None] = 'a3f9d2e1b5c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add author_is_bot column to issues table
    op.add_column('issues', sa.Column('author_is_bot', sa.Boolean(), nullable=False, server_default='false'))

    # Backfill: mark GitHub bot accounts as bots
    op.execute("UPDATE issues SET author_is_bot = TRUE WHERE author LIKE '%[bot]%' AND source = 'github'")

    # Add bot count columns to snapshots table
    op.add_column('snapshots', sa.Column('open_issues_bots', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('open_prs_bots', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_issues_bots', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_prs_bots', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('snapshots', 'closed_prs_bots')
    op.drop_column('snapshots', 'closed_issues_bots')
    op.drop_column('snapshots', 'open_prs_bots')
    op.drop_column('snapshots', 'open_issues_bots')
    op.drop_column('issues', 'author_is_bot')
