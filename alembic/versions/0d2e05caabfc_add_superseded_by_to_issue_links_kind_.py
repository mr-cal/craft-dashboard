"""add superseded_by to issue_links kind constraint

Revision ID: 0d2e05caabfc
Revises: e728675ec251
Create Date: 2026-09-18 11:25:11.721445

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0d2e05caabfc"
down_revision: str | None = "e728675ec251"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow 'superseded_by' as a valid issue_links.kind value."""
    op.drop_constraint("ck_issue_links_kind", "issue_links", type_="check")
    op.create_check_constraint(
        "ck_issue_links_kind",
        "issue_links",
        "kind IN ('likely_fixed_by', 'blocked_by', 'duplicate_of', "
        "'related_to', 'caused_by', 'superseded_by')",
    )


def downgrade() -> None:
    """Revert to the original set of allowed issue_links.kind values."""
    op.drop_constraint("ck_issue_links_kind", "issue_links", type_="check")
    op.create_check_constraint(
        "ck_issue_links_kind",
        "issue_links",
        "kind IN ('likely_fixed_by', 'blocked_by', 'duplicate_of', "
        "'related_to', 'caused_by')",
    )
