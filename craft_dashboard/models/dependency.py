"""Dependency model for tracking project dependencies."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base

if TYPE_CHECKING:
    from craft_dashboard.models.project import Project


class Dependency(Base):
    """A dependency of a project on a specific branch."""

    __tablename__ = "dependencies"
    __table_args__ = (UniqueConstraint("project_id", "branch", "dependency_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    dependency_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version_spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    installed_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latest_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    series: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_outdated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="dependencies")

    def __repr__(self) -> str:
        """Return a string representation."""
        return (
            f"<Dependency(project_id={self.project_id}, "
            f"name={self.dependency_name!r}, branch={self.branch!r})>"
        )
