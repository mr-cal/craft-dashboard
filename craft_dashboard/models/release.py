"""Release model for tracking project versions."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from craft_dashboard.models.base import Base

if TYPE_CHECKING:
    from craft_dashboard.models.project import Project


class Release(Base):
    """A release version of a project."""

    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("project_id", "branch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_hotfix: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="releases")

    @validates("branch")
    def _coalesce_branch(self, _key: str, value: str | None) -> str:
        """Coalesce NULL branch values to an empty string."""
        return value or ""

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Release(project_id={self.project_id}, version={self.version!r})>"
