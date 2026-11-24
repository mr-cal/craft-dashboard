"""add llm_evaluations issue-history index; drop duplicate hnsw index

The issue detail page fetches evaluation history per issue
(``WHERE issue_id = ? AND ... ORDER BY evaluated_at DESC``). The only
``issue_id`` index was the partial latest=True one, which this query cannot
use, so it seq-scanned the full (1M+ row) table at ~2.5s per call.

Also drops ``ix_llm_evaluations_embedding_ccnew``, a stray duplicate of
``ix_llm_evaluations_embedding`` (identical definition) that was never part
of the migration chain; duplicate HNSW indexes only add write and memory
overhead.

Revision ID: b7c9d2e4f6a8
Revises: 6103aa46d720
Create Date: 2026-11-24 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "b7c9d2e4f6a8"
down_revision: str | None = "6103aa46d720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from alembic import op

    # ASC is enough: Postgres scans a btree backwards for ORDER BY ... DESC.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_evaluations_issue_history "
        "ON llm_evaluations (issue_id, evaluated_at)"
    )
    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_embedding_ccnew")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_issue_history")
    # ix_llm_evaluations_embedding_ccnew was a stray duplicate; not recreated.
