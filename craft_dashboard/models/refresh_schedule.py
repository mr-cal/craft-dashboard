"""Refresh schedule model for tracking data collection timing."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class RefreshSchedule(Base):
    """Schedule entry tracking when to next refresh data for a project+source."""

    __tablename__ = "refresh_schedule"
    __table_args__ = (UniqueConstraint("project_id", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    next_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<RefreshSchedule(project_id={self.project_id}, "
            f"source={self.source!r}, failures={self.consecutive_failures})>"
        )
