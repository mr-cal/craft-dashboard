"""add_eval_queue_index

Revision ID: f2b1e1807a8a
Revises: acbc7fc12cd7
Create Date: 2026-07-08 14:10:00.000000

Adds a partial index to speed up the eval-next endpoint, which outer-joins
all issues to llm_evaluations filtering on eval_locked_until with no covering
index, causing slow scans similar to the embed-next issue.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b1e1807a8a"
down_revision: str | None = "acbc7fc12cd7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_llm_evaluations_eval_queue
        ON llm_evaluations (eval_locked_until, issue_id)
        WHERE latest = true
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_llm_evaluations_eval_queue")
