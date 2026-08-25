"""add open poll failure tracking to refresh schedule

Tracks the 10-minute open-issue poll's consecutive failures separately from
the weekly/hourly full-refresh's `consecutive_failures`/`last_error`. The
open poll hits far more (mostly transient/self-healing) errors on large
repos simply because it runs ~1000x more often than a full refresh, and
sharing one counter made those blips look like the full refresh itself was
broken (e.g. craft-parts/snapcraft showing "consecutive failures" driven
entirely by open-poll noise).

Revision ID: 58e6ebac628e
Revises: 4ae069e8c8f0
Create Date: 2026-08-25 21:23:29.310589

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58e6ebac628e'
down_revision: Union[str, None] = '4ae069e8c8f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'refresh_schedule', sa.Column('open_poll_last_error', sa.Text(), nullable=True)
    )
    op.add_column(
        'refresh_schedule',
        sa.Column(
            'open_poll_consecutive_failures',
            sa.Integer(),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    op.drop_column('refresh_schedule', 'open_poll_consecutive_failures')
    op.drop_column('refresh_schedule', 'open_poll_last_error')
