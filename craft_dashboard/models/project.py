"""Project model for tracked *craft repositories."""

from typing import TYPE_CHECKING

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from craft_dashboard.models.dependency import Dependency
    from craft_dashboard.models.issue import Issue
    from craft_dashboard.models.release import Release
    from craft_dashboard.models.snapshot import Snapshot


class Project(TimestampMixin, Base):
    """A tracked *craft project (application, library, or other)."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Valid categories: "application", "library"
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    github_org: Mapped[str] = mapped_column(String(255), default="canonical")
    launchpad_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The HEAD SHA the commit scanner last scanned for this project. None
    # until the first scan. The scanner's next pass runs
    # `git log --name-only <last_scanned_sha>..<new_head>`.
    last_scanned_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    issues: Mapped[list["Issue"]] = relationship(back_populates="project")
    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="project")
    releases: Mapped[list["Release"]] = relationship(back_populates="project")
    dependencies: Mapped[list["Dependency"]] = relationship(back_populates="project")

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Project(name={self.name!r}, category={self.category!r})>"
