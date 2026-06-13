"""Migrate close_outdated suggested_action to close_stale.

Revision ID: g_migrate_close_outdated_to_stale
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-13 00:00:00.000000
"""

from collections.abc import Sequence

revision: str = "g_migrate_close_outdated_to_stale"
down_revision: str | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Migrate close_outdated suggested_action to close_stale."""
    from alembic import op

    op.execute(
        "UPDATE llm_evaluations "
        "SET suggested_action = 'close_stale' "
        "WHERE suggested_action = 'close_outdated'"
    )


def downgrade() -> None:
    """Downgrade is lossy — cannot distinguish migrated rows."""
    pass
