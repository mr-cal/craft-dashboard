"""add_dep_version_columns

Revision ID: a3f9d2e1b5c8
Revises: b67acfce407c
Create Date: 2026-05-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f9d2e1b5c8"
down_revision: Union[str, None] = "b67acfce407c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dependencies", sa.Column("installed_version", sa.String(255), nullable=True))
    op.add_column("dependencies", sa.Column("latest_version", sa.String(255), nullable=True))
    op.add_column("dependencies", sa.Column("series", sa.String(64), nullable=True))
    op.add_column("dependencies", sa.Column("is_outdated", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("dependencies", "is_outdated")
    op.drop_column("dependencies", "series")
    op.drop_column("dependencies", "latest_version")
    op.drop_column("dependencies", "installed_version")
