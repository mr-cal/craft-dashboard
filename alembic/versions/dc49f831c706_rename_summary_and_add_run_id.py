"""rename issue_activities.summary to title and add collection_run_id

Revision ID: dc49f831c706
Revises: 68de7346cc6d
Create Date: 2026-07-17 23:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dc49f831c706"
down_revision: str | None = "68de7346cc6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `summary` actually held a verbatim copy of the issue title, not a
    # generated description of the change — rename it to match reality.
    op.alter_column("issue_activities", "summary", new_column_name="title")
    op.add_column(
        "issue_activities",
        sa.Column(
            "collection_run_id",
            sa.Integer(),
            sa.ForeignKey("collection_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_issue_activities_collection_run_id",
        "issue_activities",
        ["collection_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_issue_activities_collection_run_id", table_name="issue_activities"
    )
    op.drop_column("issue_activities", "collection_run_id")
    op.alter_column("issue_activities", "title", new_column_name="summary")
