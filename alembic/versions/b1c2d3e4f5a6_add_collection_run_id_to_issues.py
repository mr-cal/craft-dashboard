"""add_collection_run_id_to_issues

Revision ID: b1c2d3e4f5a6
Revises: fa02b11af7dc
Create Date: 2026-06-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "fa02b11af7dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "issues",
        sa.Column(
            "collection_run_id",
            sa.Integer(),
            sa.ForeignKey("collection_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_issues_collection_run_id",
        "issues",
        ["collection_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_issues_collection_run_id", table_name="issues")
    op.drop_column("issues", "collection_run_id")
