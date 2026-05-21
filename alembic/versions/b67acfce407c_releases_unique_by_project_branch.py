"""releases_unique_by_project_branch

Revision ID: b67acfce407c
Revises: 5d2a2a5c1823
Create Date: 2026-05-21 13:23:56.907170

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b67acfce407c'
down_revision: Union[str, None] = '5d2a2a5c1823'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM releases")
    op.drop_constraint("releases_project_id_version_key", "releases", type_="unique")
    op.create_unique_constraint("uq_releases_project_id_branch", "releases", ["project_id", "branch"])


def downgrade() -> None:
    op.drop_constraint("uq_releases_project_id_branch", "releases", type_="unique")
    op.create_unique_constraint("releases_project_id_version_key", "releases", ["project_id", "version"])
