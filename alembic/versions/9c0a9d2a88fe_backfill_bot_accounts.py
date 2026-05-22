# ruff: noqa: INP001
"""Backfill bot accounts.

Revision ID: 9c0a9d2a88fe
Revises: fa02b11af7dc
Create Date: 2026-06-03 00:00:01.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c0a9d2a88fe"
down_revision: str | None = "c3e8f1a2b9d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOT_ACCOUNTS = ("Copilot", "dependabot[bot]", "renovate[bot]")


def upgrade() -> None:
    """Mark configured GitHub bot accounts as bots."""
    quoted_accounts = ", ".join(f"'{account}'" for account in BOT_ACCOUNTS)
    op.execute(
        "UPDATE issues "
        "SET author_is_bot = TRUE "
        f"WHERE author IN ({quoted_accounts}) AND source = 'github'"
    )


def downgrade() -> None:
    """Reverse the configured bot-account backfill."""
    quoted_accounts = ", ".join(f"'{account}'" for account in BOT_ACCOUNTS)
    op.execute(
        "UPDATE issues "
        "SET author_is_bot = FALSE "
        f"WHERE author IN ({quoted_accounts}) AND source = 'github'"
    )
