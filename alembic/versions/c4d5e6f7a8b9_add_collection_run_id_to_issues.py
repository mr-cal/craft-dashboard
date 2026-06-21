"""add_collection_run_id_to_issues

Revision ID: c4d5e6f7a8b9
Revises: d1b3f5a46924
Create Date: 2026-06-21 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "d1b3f5a46924"
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
