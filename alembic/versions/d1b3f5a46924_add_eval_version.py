"""add_eval_version

Revision ID: d1b3f5a46924
Revises: ab12cd34ef56
Create Date: 2026-06-21 02:01:24.263574

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1b3f5a46924"
down_revision: str | None = "ab12cd34ef56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "llm_evaluations",
        sa.Column("eval_version", sa.Integer(), nullable=True),
    )
    # All existing evaluations were produced before versioning was introduced;
    # assign them version 1 so they can be re-evaluated as version 2 later.
    op.execute(
        "UPDATE llm_evaluations SET eval_version = 1 WHERE eval_version IS NULL"
    )


def downgrade() -> None:
    op.drop_column("llm_evaluations", "eval_version")
