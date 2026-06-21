"""Collection run health model."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base

if TYPE_CHECKING:
    from craft_dashboard.models.issue import Issue


class CollectionRun(Base):
    """Tracks a single collection run for a source."""

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    projects_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issues_collected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    # Relationships
    issues: Mapped[list["Issue"]] = relationship(back_populates="collection_run")
