"""Model for individual issue/PR change events, driving the admin activity feed."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class IssueActivity(Base):
    """A single detected change to an issue or pull request.

    Populated during the per-issue upsert in ``GitHubCollector.collect_issues``
    whenever an issue/PR's stored state differs from its previous value,
    driving the admin page's rolling "recent activity" feed.
    """

    __tablename__ = "issue_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # The issue/PR's title at the time of the change. Named `title` (not
    # `summary`) because it's a verbatim copy of the issue title, not a
    # generated description of what changed.
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # The collection run that recorded this change, if known. Nullable and
    # SET NULL on run deletion so activity history outlives run retention.
    collection_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            "<IssueActivity(project_id="
            f"{self.project_id}, issue_number={self.issue_number}, "
            f"change_type={self.change_type!r})>"
        )
