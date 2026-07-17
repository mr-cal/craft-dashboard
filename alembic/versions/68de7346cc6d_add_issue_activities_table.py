"""add issue_activities table

Revision ID: 68de7346cc6d
Revises: f2b1e1807a8a
Create Date: 2026-07-17 19:44:51.224344

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "68de7346cc6d"
down_revision: str | None = "f2b1e1807a8a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "issue_activities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_issue_activities_occurred_at"),
        "issue_activities",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_issue_activities_issue_number"),
        "issue_activities",
        ["issue_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_issue_activities_project_id"),
        "issue_activities",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_issue_activities_project_id"), table_name="issue_activities")
    op.drop_index(
        op.f("ix_issue_activities_issue_number"),
        table_name="issue_activities",
    )
    op.drop_index(
        op.f("ix_issue_activities_occurred_at"),
        table_name="issue_activities",
    )
    op.drop_table("issue_activities")
