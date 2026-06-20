"""add_summary_embedding

Revision ID: e8f1a2b9c3d4
Revises: c9f8a7b6e5d4
Create Date: 2026-06-20 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "e8f1a2b9c3d4"
down_revision: str | None = "c9f8a7b6e5d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add summary_embedding vector column and HNSW index."""
    from alembic import op

    # pgvector extension was created in migration a1b2c3d4e5f6; safe to re-run
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE llm_evaluations "
        "ADD COLUMN IF NOT EXISTS summary_embedding vector(768)"
    )
    # HNSW index for fast cosine-distance nearest-neighbour search.
    # Scoped to latest=true rows (the only ones queried at read time).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_evaluations_embedding "
        "ON llm_evaluations "
        "USING hnsw (summary_embedding vector_cosine_ops) "
        "WHERE latest = true AND summary_embedding IS NOT NULL"
    )


def downgrade() -> None:
    """Drop HNSW index and summary_embedding column."""
    from alembic import op

    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_embedding")
    op.execute(
        "ALTER TABLE llm_evaluations DROP COLUMN IF EXISTS summary_embedding"
    )
