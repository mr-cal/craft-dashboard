"""Add embedding_tokens to llm_evaluations and ingestion metrics to refresh_schedule.

Revision ID: 9e37c633701b
Revises: 0d2e05caabfc
Create Date: 2026-09-25 11:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e37c633701b"
down_revision: str | None = "0d2e05caabfc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add embedding_tokens and per-project refresh metrics columns."""
    op.add_column(
        "llm_evaluations",
        sa.Column("embedding_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "refresh_schedule",
        sa.Column("last_open_poll_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "refresh_schedule",
        sa.Column("last_open_poll_issues_collected", sa.Integer(), nullable=True),
    )
    op.add_column(
        "refresh_schedule",
        sa.Column("last_full_refresh_issues_collected", sa.Integer(), nullable=True),
    )
    op.add_column(
        "refresh_schedule",
        sa.Column("last_full_refresh_duration_seconds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    """Drop embedding_tokens and per-project refresh metrics columns."""
    op.drop_column("refresh_schedule", "last_full_refresh_duration_seconds")
    op.drop_column("refresh_schedule", "last_full_refresh_issues_collected")
    op.drop_column("refresh_schedule", "last_open_poll_issues_collected")
    op.drop_column("refresh_schedule", "last_open_poll_at")
    op.drop_column("llm_evaluations", "embedding_tokens")
