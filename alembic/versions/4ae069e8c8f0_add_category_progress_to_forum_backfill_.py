"""add category progress to forum backfill state

Replaces the old month-cursor (``earliest_month_backfilled``) with a
per-category resumable cursor (``category_progress``), needed by the
rewritten category-listing-based backfill collector.

Revision ID: 4ae069e8c8f0
Revises: 42f5d054e245
Create Date: 2026-08-24 18:28:36.336819

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4ae069e8c8f0'
down_revision: Union[str, None] = '42f5d054e245'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'forum_backfill_state',
        sa.Column(
            'category_progress',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='{}',
        ),
    )
    op.drop_column('forum_backfill_state', 'earliest_month_backfilled')


def downgrade() -> None:
    op.add_column(
        'forum_backfill_state',
        sa.Column(
            'earliest_month_backfilled',
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.drop_column('forum_backfill_state', 'category_progress')
