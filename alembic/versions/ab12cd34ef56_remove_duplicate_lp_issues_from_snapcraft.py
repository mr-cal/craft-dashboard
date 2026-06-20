"""remove_duplicate_lp_issues_from_snapcraft

Launchpad issues for snapcraft were being collected twice: once under the
"snapcraft" project and once under the "snapcraft (launchpad)" project.  The
"snapcraft (launchpad)" project is the canonical home for LP issues.  This
migration deletes the duplicates from the plain "snapcraft" project.

llm_evaluations rows cascade-delete automatically via the FK with
ondelete="CASCADE".

Revision ID: ab12cd34ef56
Revises: f9a0b1c2d3e4
Create Date: 2026-06-20 22:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "ab12cd34ef56"
down_revision: str | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM issues
        WHERE id IN (
            SELECT i.id
            FROM issues i
            JOIN projects p ON p.id = i.project_id
            WHERE p.name = 'snapcraft' AND i.source = 'launchpad'
        )
    """)


def downgrade() -> None:
    # Deleted data cannot be restored.
    pass
