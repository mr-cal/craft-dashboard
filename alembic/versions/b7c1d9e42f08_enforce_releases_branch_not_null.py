"""Enforce NOT NULL on releases.branch

The model declares ``branch`` as non-nullable with a ``''`` default and a
validator that coalesces ``None``, but the column was created nullable, so the
database and the model disagreed. Existing NULLs are coalesced to ``''``, which
is what the validator would have produced.

Revision ID: b7c1d9e42f08
Revises: 9e37c633701b
Create Date: 2026-09-29

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c1d9e42f08"
down_revision = "9e37c633701b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Coalesce NULL branches and make the column NOT NULL."""
    op.execute("UPDATE releases SET branch = '' WHERE branch IS NULL")
    op.alter_column(
        "releases",
        "branch",
        existing_type=sa.String(length=255),
        nullable=False,
    )


def downgrade() -> None:
    """Allow NULL branches again."""
    op.alter_column(
        "releases",
        "branch",
        existing_type=sa.String(length=255),
        nullable=True,
    )
