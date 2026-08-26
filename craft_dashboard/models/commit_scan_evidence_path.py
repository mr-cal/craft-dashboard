"""Reverse index: which issue's evidence touched which (project, path).

Populated by Phase 4's tool layer, whenever an evaluation reads a file or
greps a repo — every tool records the (repo, path) pairs it touched. The
commit scanner queries this table to find issues whose evidence overlaps a
newly changed path, without needing to replay each issue's evidence.

This table is created in Phase 3 (rather than deferred to Phase 4) so the
path-intersection invalidation query can be fully implemented and tested
now; it is simply empty in production until Phase 4 evaluations begin
writing to it.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class CommitScanEvidencePath(Base):
    """One (issue, project, path) triple an evaluation's evidence touched."""

    __tablename__ = "commit_scan_evidence_paths"
    __table_args__ = (
        Index("ix_commit_scan_evidence_paths_project_path", "project", "path"),
        Index("ix_commit_scan_evidence_paths_issue_id", "issue_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("issues.id", ondelete="CASCADE"), nullable=False
    )
    project: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<CommitScanEvidencePath(project={self.project!r}, path={self.path!r})>"
