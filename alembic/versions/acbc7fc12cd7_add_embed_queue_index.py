"""add_embed_queue_index

Revision ID: acbc7fc12cd7
Revises: fa02b11af7dc
Create Date: 2026-07-08 14:00:00.000000

Adds a partial index to speed up the embed-next endpoint, which was
scanning the full latest=true rows (~18k) to find rows with
summary_embedding IS NULL, causing 20+ second response times.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "acbc7fc12cd7"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_llm_evaluations_embed_queue
        ON llm_evaluations (issue_id)
        WHERE latest = true
          AND model_name != 'pending'
          AND summary IS NOT NULL
          AND summary != ''
          AND summary_embedding IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_embed_queue")
