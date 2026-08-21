"""add_issue_search_embedding

Revision ID: 106bfe752b75
Revises: 3bf712e6582b
Create Date: 2026-08-21 15:33:02.950483

"""

from collections.abc import Sequence

revision: str = "106bfe752b75"
down_revision: str | None = "3bf712e6582b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add issues.search_embedding vector column and HNSW index."""
    from alembic import op

    # pgvector extension was created in migration a1b2c3d4e5f6; safe to re-run
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        "ALTER TABLE issues ADD COLUMN IF NOT EXISTS search_embedding vector(1024)"
    )
    # HNSW index for fast cosine-distance nearest-neighbour search over
    # issue title+body embeddings (semantic issue search).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_issues_search_embedding "
        "ON issues "
        "USING hnsw (search_embedding vector_cosine_ops) "
        "WHERE search_embedding IS NOT NULL"
    )


def downgrade() -> None:
    """Drop HNSW index and search_embedding column."""
    from alembic import op

    op.execute("DROP INDEX IF EXISTS ix_issues_search_embedding")
    op.execute("ALTER TABLE issues DROP COLUMN IF EXISTS search_embedding")
