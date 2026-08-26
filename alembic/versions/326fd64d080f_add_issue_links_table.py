"""add issue links table

Revision ID: 326fd64d080f
Revises: 58e6ebac628e
Create Date: 2026-08-26 15:40:31.394315

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "326fd64d080f"
down_revision: str | None = "58e6ebac628e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the issue_links table."""
    op.create_table(
        "issue_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_issue_id", sa.Integer(), nullable=False),
        sa.Column("llm_evaluation_id", sa.Integer(), nullable=False),
        sa.Column("to_issue_id", sa.Integer(), nullable=True),
        sa.Column("to_ref", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "confidence",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('likely_fixed_by', 'blocked_by', 'duplicate_of', "
            "'related_to', 'caused_by')",
            name="ck_issue_links_kind",
        ),
        sa.CheckConstraint(
            "source IN ('evaluator', 'duplicate_detector')",
            name="ck_issue_links_source",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_issue_links_confidence_range",
        ),
        sa.ForeignKeyConstraint(["from_issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["llm_evaluation_id"], ["llm_evaluations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["to_issue_id"], ["issues.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_issue_links_from_issue_id"), "issue_links", ["from_issue_id"]
    )
    op.create_index(op.f("ix_issue_links_to_issue_id"), "issue_links", ["to_issue_id"])


def downgrade() -> None:
    """Drop the issue_links table."""
    op.drop_index(op.f("ix_issue_links_to_issue_id"), table_name="issue_links")
    op.drop_index(op.f("ix_issue_links_from_issue_id"), table_name="issue_links")
    op.drop_table("issue_links")
