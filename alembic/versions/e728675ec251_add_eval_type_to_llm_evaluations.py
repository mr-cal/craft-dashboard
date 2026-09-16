"""add eval_type to llm_evaluations

Revision ID: e728675ec251
Revises: 7f8e9d0c1b2a
Create Date: 2026-09-16 15:22:39.239745

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e728675ec251'
down_revision: Union[str, None] = '7f8e9d0c1b2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add eval_type column and backfill summary evaluations."""
    op.add_column(
        "llm_evaluations",
        sa.Column(
            "eval_type",
            sa.String(length=20),
            nullable=False,
            server_default="scoring",
        ),
    )
    op.execute(
        """
        UPDATE llm_evaluations
        SET eval_type = 'summary'
        WHERE suggested_action IS NULL
          AND (scores IS NULL OR scores = '{}'::jsonb)
        """
    )


def downgrade() -> None:
    """Drop eval_type column from llm_evaluations."""
    op.drop_column("llm_evaluations", "eval_type")
