"""reconcile_launchpad_issue_links

Revision ID: 93c92e7f90f1
Revises: 929a73da054d
Create Date: 2026-10-21 18:28:34.769039

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '93c92e7f90f1'
down_revision: Union[str, None] = '929a73da054d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Reconcile unlinked Launchpad bugs in issue_links table."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("""
            UPDATE issue_links il
            SET to_issue_id = i.id,
                to_ref = 'snapcraft (launchpad)#' || split_part(il.to_ref, '#', 2)
            FROM issues i
            JOIN projects p ON i.project_id = p.id
            WHERE il.to_issue_id IS NULL
              AND p.name = 'snapcraft (launchpad)'
              AND il.to_ref ~ '^snapcraft#[0-9]+$'
              AND i.external_id = split_part(il.to_ref, '#', 2);
        """)
    else:
        op.execute("""
            UPDATE issue_links
            SET to_issue_id = (
                SELECT i.id
                FROM issues i
                JOIN projects p ON i.project_id = p.id
                WHERE p.name = 'snapcraft (launchpad)'
                  AND i.external_id = SUBSTR(issue_links.to_ref, INSTR(issue_links.to_ref, '#') + 1)
            ),
            to_ref = 'snapcraft (launchpad)#' || SUBSTR(issue_links.to_ref, INSTR(issue_links.to_ref, '#') + 1)
            WHERE to_issue_id IS NULL
              AND to_ref LIKE 'snapcraft#%'
              AND EXISTS (
                SELECT 1
                FROM issues i
                JOIN projects p ON i.project_id = p.id
                WHERE p.name = 'snapcraft (launchpad)'
                  AND i.external_id = SUBSTR(issue_links.to_ref, INSTR(issue_links.to_ref, '#') + 1)
              );
        """)


def downgrade() -> None:
    pass
