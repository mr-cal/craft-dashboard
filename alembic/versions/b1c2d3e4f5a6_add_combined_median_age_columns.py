# ruff: noqa: INP001
"""Add combined median age columns.

Revision ID: b1c2d3e4f5a6
Revises: 9c0a9d2a88fe
Create Date: 2026-06-10 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "9c0a9d2a88fe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add combined issue/PR median age columns to snapshots."""
    op.add_column(
        "snapshots",
        sa.Column("median_age", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "snapshots",
        sa.Column("nm_median_age", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "snapshots",
        sa.Column(
            "median_age_internal", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "snapshots",
        sa.Column("median_age_bots", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Drop combined issue/PR median age columns from snapshots."""
    op.drop_column("snapshots", "median_age_bots")
    op.drop_column("snapshots", "median_age_internal")
    op.drop_column("snapshots", "nm_median_age")
    op.drop_column("snapshots", "median_age")
