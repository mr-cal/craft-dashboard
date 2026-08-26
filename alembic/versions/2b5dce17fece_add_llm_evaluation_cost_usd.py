"""Add llm_evaluation cost_usd.

Revision ID: 2b5dce17fece
Revises: 326fd64d080f
Create Date: 2026-08-26 15:56:40.348130

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2b5dce17fece"
down_revision: str | None = "326fd64d080f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cost_usd to llm_evaluations."""
    op.add_column("llm_evaluations", sa.Column("cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    """Remove cost_usd from llm_evaluations."""
    op.drop_column("llm_evaluations", "cost_usd")
