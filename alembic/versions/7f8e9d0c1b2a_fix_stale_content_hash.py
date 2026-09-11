"""fix stale content_hash for closed issues

Revision ID: 7f8e9d0c1b2a
Revises: 04b321505716
Create Date: 2026-09-11 17:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f8e9d0c1b2a"
down_revision: str | None = "04b321505716"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Clear stale content_hash for closed issues so they get recomputed dynamically."""
    # When an issue was closed via reconciliation or before content_hash calculation
    # accounted for state transitions, content_hash remained set to the hash of the 'open' state.
    # Clearing content_hash for closed issues allows _current_content_hash to compute the
    # current accurate hash on the fly and self-heal the DB column.
    op.execute(
        "UPDATE issues SET content_hash = NULL WHERE state = 'closed'"
    )


def downgrade() -> None:
    pass
