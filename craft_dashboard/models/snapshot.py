"""Daily snapshot model for tracking issue/PR trends over time."""

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class Snapshot(Base):
    """A daily snapshot of open issue and PR counts for a project."""

    __tablename__ = "snapshots"
    __table_args__ = (UniqueConstraint("project_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    open_prs: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_external: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_internal: Mapped[int] = mapped_column(Integer, default=0)
    open_prs_external: Mapped[int] = mapped_column(Integer, default=0)
    open_prs_internal: Mapped[int] = mapped_column(Integer, default=0)
    open_bugs: Mapped[int] = mapped_column(Integer, default=0)

    # Median ages (in days)
    median_issue_age: Mapped[int] = mapped_column(Integer, default=0)
    median_pr_age: Mapped[int] = mapped_column(Integer, default=0)
    nm_median_issue_age: Mapped[int] = mapped_column(Integer, default=0)
    nm_median_pr_age: Mapped[int] = mapped_column(Integer, default=0)
    median_issue_age_internal: Mapped[int] = mapped_column(Integer, default=0)
    median_pr_age_internal: Mapped[int] = mapped_column(Integer, default=0)
    median_issue_age_bots: Mapped[int] = mapped_column(Integer, default=0)
    median_pr_age_bots: Mapped[int] = mapped_column(Integer, default=0)

    # Combined median ages (issues + PRs together, matches starcraft-stats)
    median_age: Mapped[int] = mapped_column(Integer, default=0)
    nm_median_age: Mapped[int] = mapped_column(Integer, default=0)
    median_age_internal: Mapped[int] = mapped_column(Integer, default=0)
    median_age_bots: Mapped[int] = mapped_column(Integer, default=0)

    # Closed counts
    closed_issues: Mapped[int] = mapped_column(Integer, default=0)
    closed_prs: Mapped[int] = mapped_column(Integer, default=0)
    closed_issues_external: Mapped[int] = mapped_column(Integer, default=0)
    closed_issues_internal: Mapped[int] = mapped_column(Integer, default=0)
    closed_prs_external: Mapped[int] = mapped_column(Integer, default=0)
    closed_prs_internal: Mapped[int] = mapped_column(Integer, default=0)

    # Bot counts
    open_issues_bots: Mapped[int] = mapped_column(Integer, default=0)
    open_prs_bots: Mapped[int] = mapped_column(Integer, default=0)
    closed_issues_bots: Mapped[int] = mapped_column(Integer, default=0)
    closed_prs_bots: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def median_issue_age_external(self) -> int:
        """Alias for nm_median_issue_age (non-maintainer = external)."""
        return self.nm_median_issue_age

    @property
    def median_pr_age_external(self) -> int:
        """Alias for nm_median_pr_age (non-maintainer = external)."""
        return self.nm_median_pr_age

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="snapshots")  # noqa: F821

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Snapshot(project_id={self.project_id}, date={self.snapshot_date})>"
