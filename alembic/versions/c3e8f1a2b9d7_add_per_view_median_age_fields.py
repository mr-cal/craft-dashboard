"""add_per_view_median_age_fields

Revision ID: c3e8f1a2b9d7
Revises: fa02b11af7dc
Create Date: 2026-06-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e8f1a2b9d7"
down_revision: Union[str, None] = "fa02b11af7dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("nm_median_issue_age", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("snapshots", sa.Column("nm_median_pr_age", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("snapshots", sa.Column("median_issue_age_internal", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("snapshots", sa.Column("median_pr_age_internal", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("snapshots", sa.Column("median_issue_age_bots", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("snapshots", sa.Column("median_pr_age_bots", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("snapshots", "median_pr_age_bots")
    op.drop_column("snapshots", "median_issue_age_bots")
    op.drop_column("snapshots", "median_pr_age_internal")
    op.drop_column("snapshots", "median_issue_age_internal")
    op.drop_column("snapshots", "nm_median_pr_age")
    op.drop_column("snapshots", "nm_median_issue_age")
