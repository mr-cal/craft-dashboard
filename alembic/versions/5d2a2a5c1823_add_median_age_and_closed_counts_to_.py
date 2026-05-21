"""add_median_age_and_closed_counts_to_snapshots

Revision ID: 5d2a2a5c1823
Revises: d89e58e0a23e
Create Date: 2026-05-21 11:58:59.588804

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d2a2a5c1823'
down_revision: Union[str, None] = 'd89e58e0a23e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('snapshots', sa.Column('median_issue_age', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('median_pr_age', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_issues', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_prs', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_issues_external', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_issues_internal', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_prs_external', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('snapshots', sa.Column('closed_prs_internal', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('snapshots', 'closed_prs_internal')
    op.drop_column('snapshots', 'closed_prs_external')
    op.drop_column('snapshots', 'closed_issues_internal')
    op.drop_column('snapshots', 'closed_issues_external')
    op.drop_column('snapshots', 'closed_prs')
    op.drop_column('snapshots', 'closed_issues')
    op.drop_column('snapshots', 'median_pr_age')
    op.drop_column('snapshots', 'median_issue_age')
