"""resize_summary_embedding_to_1024

Revision ID: f9a0b1c2d3e4
Revises: e8f1a2b9c3d4
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "e8f1a2b9c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Resize summary_embedding from vector(768) to vector(1024).

    mxbai-embed-large produces 1024-dimensional vectors.
    Existing NULL rows are unaffected; any stored 768-dim vectors are
    cleared (SET NULL) before the type change so Postgres can cast safely.
    """
    from alembic import op

    # Clear any stored embeddings with wrong dimension before altering type.
    op.execute(
        "UPDATE llm_evaluations SET summary_embedding = NULL "
        "WHERE summary_embedding IS NOT NULL"
    )
    # Drop the old HNSW index (it was built for vector(768)).
    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_embedding")
    # Alter column type.
    op.execute(
        "ALTER TABLE llm_evaluations "
        "ALTER COLUMN summary_embedding TYPE vector(1024) "
        "USING summary_embedding::vector(1024)"
    )
    # Recreate HNSW index for the new dimension.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_evaluations_embedding "
        "ON llm_evaluations "
        "USING hnsw (summary_embedding vector_cosine_ops) "
        "WHERE latest = true AND summary_embedding IS NOT NULL"
    )


def downgrade() -> None:
    """Resize summary_embedding back to vector(768)."""
    from alembic import op

    op.execute(
        "UPDATE llm_evaluations SET summary_embedding = NULL "
        "WHERE summary_embedding IS NOT NULL"
    )
    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_embedding")
    op.execute(
        "ALTER TABLE llm_evaluations "
        "ALTER COLUMN summary_embedding TYPE vector(768) "
        "USING summary_embedding::vector(768)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_evaluations_embedding "
        "ON llm_evaluations "
        "USING hnsw (summary_embedding vector_cosine_ops) "
        "WHERE latest = true AND summary_embedding IS NOT NULL"
    )
