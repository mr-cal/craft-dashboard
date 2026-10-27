"""add_ci_failure_tracking

Revision ID: 48f6015e1323
Revises: 93c92e7f90f1
Create Date: 2026-10-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "48f6015e1323"
down_revision: str | None = "93c92e7f90f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ci_workflow_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), nullable=False),
        sa.Column("workflow_name", sa.String(length=255), nullable=False),
        sa.Column("head_branch", sa.String(length=255), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("event", sa.String(length=50), nullable=False),
        sa.Column("is_fork_pr", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("conclusion", sa.String(length=20), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gh_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("html_url", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "run_id", "run_attempt"),
    )
    op.create_index(
        op.f("ix_ci_workflow_runs_project_id"),
        "ci_workflow_runs",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_ci_workflow_runs_run_id"), "ci_workflow_runs", ["run_id"]
    )
    op.create_index(
        op.f("ix_ci_workflow_runs_workflow_name"),
        "ci_workflow_runs",
        ["workflow_name"],
    )
    op.create_index(
        op.f("ix_ci_workflow_runs_head_sha"), "ci_workflow_runs", ["head_sha"]
    )
    op.create_index(
        op.f("ix_ci_workflow_runs_last_fetched_at"),
        "ci_workflow_runs",
        ["last_fetched_at"],
    )

    op.create_table(
        "ci_job_failures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("job_name", sa.String(length=255), nullable=False),
        sa.Column("job_variant", sa.String(length=255), nullable=True),
        sa.Column("step_name", sa.String(length=255), nullable=True),
        sa.Column("conclusion", sa.String(length=20), nullable=False),
        sa.Column("html_url", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["ci_workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ci_job_failures_workflow_run_id"),
        "ci_job_failures",
        ["workflow_run_id"],
    )
    op.create_index(
        op.f("ix_ci_job_failures_project_id"), "ci_job_failures", ["project_id"]
    )
    op.create_index(
        op.f("ix_ci_job_failures_job_id"), "ci_job_failures", ["job_id"]
    )
    op.create_index(
        op.f("ix_ci_job_failures_job_name"), "ci_job_failures", ["job_name"]
    )
    op.create_index(
        op.f("ix_ci_job_failures_created_at"), "ci_job_failures", ["created_at"]
    )

    op.create_table(
        "ci_test_failures",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_failure_id", sa.Integer(), nullable=False),
        sa.Column("test_type", sa.String(length=20), nullable=False),
        sa.Column("signature", sa.String(length=500), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_environmental", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_failure_id"], ["ci_job_failures.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ci_test_failures_job_failure_id"),
        "ci_test_failures",
        ["job_failure_id"],
    )
    op.create_index(
        op.f("ix_ci_test_failures_test_type"), "ci_test_failures", ["test_type"]
    )
    op.create_index(
        op.f("ix_ci_test_failures_signature"), "ci_test_failures", ["signature"]
    )

    op.create_table(
        "ci_failure_daily_counts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=500), nullable=False),
        sa.Column("branch_class", sa.String(length=10), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "signature", "branch_class", "day"),
    )
    op.create_index(
        op.f("ix_ci_failure_daily_counts_project_id"),
        "ci_failure_daily_counts",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_ci_failure_daily_counts_signature"),
        "ci_failure_daily_counts",
        ["signature"],
    )
    op.create_index(
        op.f("ix_ci_failure_daily_counts_day"), "ci_failure_daily_counts", ["day"]
    )


def downgrade() -> None:
    op.drop_table("ci_failure_daily_counts")
    op.drop_table("ci_test_failures")
    op.drop_table("ci_job_failures")
    op.drop_table("ci_workflow_runs")
