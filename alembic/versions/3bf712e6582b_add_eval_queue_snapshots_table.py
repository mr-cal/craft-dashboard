"""add eval queue snapshots table

Revision ID: 3bf712e6582b
Revises: 38a9bb474e97
Create Date: 2026-07-24 00:15:10.597013

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3bf712e6582b"
down_revision: str | None = "38a9bb474e97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the eval_queue_snapshots table used to chart queue depth over time."""
    op.create_table(
        "eval_queue_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("total_open", sa.Integer(), nullable=False),
        sa.Column("evaluated_today", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_eval_queue_snapshots_captured_at"),
        "eval_queue_snapshots",
        ["captured_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the eval_queue_snapshots table."""
    op.drop_index(
        op.f("ix_eval_queue_snapshots_captured_at"),
        table_name="eval_queue_snapshots",
    )
    op.drop_table("eval_queue_snapshots")
