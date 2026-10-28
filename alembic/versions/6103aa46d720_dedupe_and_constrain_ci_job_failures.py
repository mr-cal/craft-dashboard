"""dedupe_and_constrain_ci_job_failures

Revision ID: 6103aa46d720
Revises: 48f6015e1323
Create Date: 2026-10-28 01:45:09.568978

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6103aa46d720"
down_revision: str | None = "48f6015e1323"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Dedupe re-inserted job failures, then enforce one row per job.

    A still-failing job stays "rerun-eligible" for 7 days and gets
    refetched by every 10-minute collection pass until it ages out; before
    this constraint existed, each pass re-inserted a fresh CIJobFailure row
    for the same (workflow_run_id, job_id), inflating occurrence counts past
    the total run count. Keep the earliest row (and its parsed
    CITestFailure children via ON DELETE CASCADE) per job, then add the
    constraint that would have prevented the duplicates going forward.
    """
    op.execute("""
        DELETE FROM ci_job_failures
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY workflow_run_id, job_id
                           ORDER BY created_at ASC, id ASC
                       ) AS rn
                FROM ci_job_failures
            ) sub
            WHERE sub.rn > 1
        )
    """)
    op.create_unique_constraint(
        "uq_ci_job_failures_workflow_run_id_job_id",
        "ci_job_failures",
        ["workflow_run_id", "job_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ci_job_failures_workflow_run_id_job_id",
        "ci_job_failures",
        type_="unique",
    )
