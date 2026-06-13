"""remove_duplicate_detection_columns and clean_scores

Revision ID: 0a1b2c3d4e5f
Revises: a1b2c3d4e5f6
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

revision: str = "0a1b2c3d4e5f"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop duplicate detection columns and clean JSONB scores.

    Phase 1: Remove physical duplicate detection columns from llm_evaluations.
    Phase 2: Clean existing scores JSONB of duplicateness and readiness keys.
    """
    from alembic import op
    import sqlalchemy as sa

    # --- Phase 1: Drop columns and index ---

    # Drop the HNSW index first (columns depend on it)
    op.execute(
        "DROP INDEX IF EXISTS ix_llm_evaluations_embedding"
    )

    # Drop the duplicate detection columns (order does not matter since
    # they are independent)
    op.drop_column("llm_evaluations", "duplicate_of_issue_id")
    op.drop_column("llm_evaluations", "duplicate_locked_until")
    op.drop_column("llm_evaluations", "candidates_compared")
    op.drop_column("llm_evaluations", "summary_embedding")

    # --- Phase 2: Clean JSONB scores ---

    # Remove 'duplicateness' and 'readiness' keys from existing score objects.
    # Use - '{}' syntax to delete specific keys from JSONB.
    # The order of subtraction does not matter since the keys are independent.
    op.execute(
        "UPDATE llm_evaluations "
        "SET scores = scores - 'duplicateness' "
        "WHERE scores ? 'duplicateness'"
    )
    op.execute(
        "UPDATE llm_evaluations "
        "SET scores = scores - 'readiness' "
        "WHERE scores ? 'readiness'"
    )
