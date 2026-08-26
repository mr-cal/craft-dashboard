"""Commit scan run model: per-pass, per-project scanner observability.

Records, per project per pass: timestamp, commits scanned, sha before/after,
duration, and invalidation counts broken out per signal. Semantic matching
is the signal most likely to be over-enthusiastic, and this per-signal
breakdown identifies which dial (K, threshold) to turn when invalidation
volume spikes. See design doc section 4, "Scanner observability".
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class CommitScanRun(Base):
    """One commit-scanner pass over one project."""

    __tablename__ = "commit_scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    commits_scanned: Mapped[int] = mapped_column(Integer, nullable=False)
    sha_before: Mapped[str] = mapped_column(String(40), nullable=False)
    sha_after: Mapped[str] = mapped_column(String(40), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    invalidated_qualified_ref: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    invalidated_path: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    invalidated_semantic: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    invalidated_bare_ref: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    invalidated_launchpad: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<CommitScanRun(project_id={self.project_id}, "
            f"commits_scanned={self.commits_scanned})>"
        )
