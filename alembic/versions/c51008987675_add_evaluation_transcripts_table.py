"""add evaluation transcripts table

Revision ID: c51008987675
Revises: 2b5dce17fece
Create Date: 2026-08-26 16:17:57.582380

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c51008987675"
down_revision: str | None = "2b5dce17fece"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the evaluation_transcripts table."""
    op.create_table(
        "evaluation_transcripts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("llm_evaluation_id", sa.Integer(), nullable=False),
        sa.Column(
            "rounds",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "full_capture",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column(
            "rounds_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["llm_evaluation_id"], ["llm_evaluations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_evaluation_transcripts_llm_evaluation_id"),
        "evaluation_transcripts",
        ["llm_evaluation_id"],
    )


def downgrade() -> None:
    """Drop the evaluation_transcripts table."""
    op.drop_index(
        op.f("ix_evaluation_transcripts_llm_evaluation_id"),
        table_name="evaluation_transcripts",
    )
    op.drop_table("evaluation_transcripts")
