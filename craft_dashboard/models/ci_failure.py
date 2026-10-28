"""CI failure tracking models: workflow runs, job failures, and test signatures.

Three levels mirror what GitHub Actions actually reports (see
``plans/44-ci-failure-catalog.md``): a workflow run (pass or fail), a failed
job within that run, and 0-N parsed test/tool failure signatures within a
failed job. ``CIWorkflowRun`` rows are written for every run (not just
failures) because the fail-rate denominator for a signature is "how many
times did this workflow run at all", not just "how many times did it fail".

``CIFailureDailyCount`` is the rollup target for ``services.ci_gc`` --
the only piece of history that survives once per-run detail is pruned after
the retention window.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class CIWorkflowRun(Base):
    """One GitHub Actions workflow run (any outcome, including success).

    Unique per ``(project_id, run_id, run_attempt)`` rather than per
    ``run_id`` alone: a rerun keeps the same ``run_id`` but bumps
    ``run_attempt``, and flip-flop detection needs each attempt's own
    outcome, not just the latest one.
    """

    __tablename__ = "ci_workflow_runs"
    __table_args__ = (UniqueConstraint("project_id", "run_id", "run_attempt"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    run_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    head_branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    # "push", "pull_request", "pull_request_target", "merge_group",
    # "schedule", "workflow_dispatch", etc. Combined with head_branch against
    # the project's configured trunk-branches to derive branch_class
    # (main/pr/other) at query time -- see repositories/ci_repository.py.
    event: Mapped[str] = mapped_column(String(50), nullable=False)
    is_fork_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    conclusion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # GitHub's own updated_at for this run/attempt, used to detect reruns of
    # an already-completed run.
    gh_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When *we* last fetched this row. Drives the watermark re-derivation
    # described in the plan's "Watermarking" section: the next pass's fetch
    # floor is the oldest last_fetched_at among runs still rerun-eligible or
    # incomplete, mirroring collectors/github.py's oldest_stale_last_fetched.
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    html_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<CIWorkflowRun(project_id={self.project_id}, run_id={self.run_id}, "
            f"run_attempt={self.run_attempt}, conclusion={self.conclusion!r})>"
        )


class CIJobFailure(Base):
    """A failed job within a workflow run, with the step that failed.

    Unique per ``(workflow_run_id, job_id)``: a still-failing job stays
    "rerun-eligible" (see ``_RERUN_GRACE_DAYS`` in ``ci_collector.py``) and
    gets refetched by every collection pass for up to 7 days, so without
    this constraint the same job failure would be re-inserted on every pass
    and silently inflate occurrence counts past the total run count.
    """

    __tablename__ = "ci_job_failures"
    __table_args__ = (UniqueConstraint("workflow_run_id", "job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("ci_workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    job_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Matrix cell value split out of job_name (e.g. "openstack:ubuntu-24.04-64"
    # from "integration-spread-tests (openstack:ubuntu-24.04-64)") so matrix
    # cells of the same job are grouped together rather than treated as
    # unrelated jobs. None for non-matrix jobs.
    job_variant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    step_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    conclusion: Mapped[str] = mapped_column(String(20), nullable=False)
    html_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<CIJobFailure(job_name={self.job_name!r}, step_name={self.step_name!r})>"
        )


class CITestFailure(Base):
    """A parsed test/tool failure signature within a failed job.

    No project_id column: reachable via job_failure_id -> ci_job_failures
    -> project_id, which keeps this (potentially high-volume, per-run-text)
    table as narrow as possible.
    """

    __tablename__ = "ci_test_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_failure_id: Mapped[int] = mapped_column(
        ForeignKey("ci_job_failures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "pytest", "spread", "go", "linter", "infra"
    test_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    signature: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_environmental: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<CITestFailure(signature={self.signature!r}, test_type={self.test_type!r})>"


class CIFailureDailyCount(Base):
    """Permanent daily rollup of occurrence/total counts per signature.

    Written by ``services.ci_gc`` before per-run detail rows are pruned past
    the retention window, so historical trend charts keep working (and
    main-vs-PR history stays distinguishable) after the detail is gone.
    """

    __tablename__ = "ci_failure_daily_counts"
    __table_args__ = (
        UniqueConstraint("project_id", "signature", "branch_class", "day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    signature: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    branch_class: Mapped[str] = mapped_column(String(10), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<CIFailureDailyCount(signature={self.signature!r}, day={self.day}, "
            f"fail_count={self.fail_count}, total_count={self.total_count})>"
        )
