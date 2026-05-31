"""add_duplicate_detection_columns

Revision ID: a1b2c3d4e5f6
Revises: f2a3b4c5d6e7
Create Date: 2026-05-31 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add pgvector extension and duplicate detection columns."""
    from alembic import op

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "llm_evaluations",
        sa.Column("summary_embedding", sa.Text(), nullable=True),
    )
    op.add_column(
        "llm_evaluations",
        sa.Column("candidates_compared", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_evaluations",
        sa.Column(
            "duplicate_locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "llm_evaluations",
        sa.Column("duplicate_of_issue_id", sa.Integer(), nullable=True),
    )

    # Change summary_embedding from Text to vector(768) after extension is created
    op.execute(
        "ALTER TABLE llm_evaluations "
        "ALTER COLUMN summary_embedding TYPE vector(768) "
        "USING summary_embedding::vector(768)"
    )

    # HNSW index for fast cosine similarity search (latest evaluations only)
    op.execute(
        "CREATE INDEX ix_llm_evaluations_embedding "
        "ON llm_evaluations "
        "USING hnsw (summary_embedding vector_cosine_ops) "
        "WHERE latest = true AND summary_embedding IS NOT NULL"
    )


def downgrade() -> None:
    """Remove duplicate detection columns."""
    from alembic import op

    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_embedding")
    op.drop_column("llm_evaluations", "duplicate_of_issue_id")
    op.drop_column("llm_evaluations", "duplicate_locked_until")
    op.drop_column("llm_evaluations", "candidates_compared")
    op.drop_column("llm_evaluations", "summary_embedding")
