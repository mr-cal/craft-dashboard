"""add evidence_generation and commit scanner tables

Revision ID: 04b321505716
Revises: c51008987675
Create Date: 2026-08-26 18:18:36.753992

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "04b321505716"
down_revision: str | None = "c51008987675"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add evidence-generation columns and commit scanner tables."""
    op.create_table(
        "commit_scan_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commits_scanned", sa.Integer(), nullable=False),
        sa.Column("sha_before", sa.String(length=40), nullable=False),
        sa.Column("sha_after", sa.String(length=40), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("invalidated_qualified_ref", sa.Integer(), nullable=False),
        sa.Column("invalidated_path", sa.Integer(), nullable=False),
        sa.Column("invalidated_semantic", sa.Integer(), nullable=False),
        sa.Column("invalidated_bare_ref", sa.Integer(), nullable=False),
        sa.Column("invalidated_launchpad", sa.Integer(), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_commit_scan_runs_project_id"),
        "commit_scan_runs",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "commit_scan_evidence_paths",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("project", sa.String(length=255), nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_commit_scan_evidence_paths_issue_id",
        "commit_scan_evidence_paths",
        ["issue_id"],
        unique=False,
    )
    op.create_index(
        "ix_commit_scan_evidence_paths_project_path",
        "commit_scan_evidence_paths",
        ["project", "path"],
        unique=False,
    )
    op.add_column(
        "issues",
        sa.Column("evidence_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "llm_evaluations",
        sa.Column("evidence_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "projects", sa.Column("last_scanned_sha", sa.String(length=40), nullable=True)
    )


def downgrade() -> None:
    """Drop commit scanner tables and evidence-generation columns."""
    op.drop_column("projects", "last_scanned_sha")
    op.drop_column("llm_evaluations", "evidence_generation")
    op.drop_column("issues", "evidence_generation")
    op.drop_index(
        "ix_commit_scan_evidence_paths_project_path",
        table_name="commit_scan_evidence_paths",
    )
    op.drop_index(
        "ix_commit_scan_evidence_paths_issue_id",
        table_name="commit_scan_evidence_paths",
    )
    op.drop_table("commit_scan_evidence_paths")
    op.drop_index(op.f("ix_commit_scan_runs_project_id"), table_name="commit_scan_runs")
    op.drop_table("commit_scan_runs")
