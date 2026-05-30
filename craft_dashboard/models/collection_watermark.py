"""Collection watermark model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from craft_dashboard.models.base import Base


class CollectionWatermark(Base):
    """Tracks the last successful collection time per project and source."""

    __tablename__ = "collection_watermarks"
    __table_args__ = (UniqueConstraint("project_id", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    last_collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
