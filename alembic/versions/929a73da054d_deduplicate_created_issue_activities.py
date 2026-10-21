"""deduplicate_created_issue_activities

Revision ID: 929a73da054d
Revises: 444063d1b5b0
Create Date: 2026-10-21 16:28:36.305712

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '929a73da054d'
down_revision: Union[str, None] = '444063d1b5b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Delete duplicate 'created' entries in issue_activities, keeping the earliest per issue."""
    op.execute("""
        DELETE FROM issue_activities
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY project_id, issue_number, change_type
                           ORDER BY occurred_at ASC, id ASC
                       ) AS rn
                FROM issue_activities
                WHERE change_type = 'created'
            ) sub
            WHERE sub.rn > 1
        )
    """)


def downgrade() -> None:
    # Deleted duplicate history cannot be restored.
    pass
