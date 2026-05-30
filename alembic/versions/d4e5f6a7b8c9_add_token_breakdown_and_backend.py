"""add_token_breakdown_and_llm_backend

Revision ID: d4e5f6a7b8c9
Revises: b1c2d3e4f5a6
Create Date: 2026-05-30 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_evaluations",
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_evaluations",
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_evaluations",
        sa.Column("llm_backend", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_evaluations", "llm_backend")
    op.drop_column("llm_evaluations", "completion_tokens")
    op.drop_column("llm_evaluations", "prompt_tokens")
