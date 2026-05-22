"""Issue and Pull Request model."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from craft_dashboard.models.base import Base


class Issue(Base):
    """An issue or pull request from GitHub or Launchpad."""

    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("project_id", "source", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_is_maintainer: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    author_is_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    labels: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    comments: Mapped[list] = mapped_column(JSONB, nullable=True, default=list)
    last_fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="issues")  # noqa: F821
    evaluations: Mapped[list["LLMEvaluation"]] = relationship(  # noqa: F821
        back_populates="issue",
        order_by="LLMEvaluation.evaluated_at.desc()",
    )

    @property
    def latest_evaluation(self) -> "LLMEvaluation | None":  # noqa: F821
        """Return the most recent LLM evaluation, or None."""
        return next((e for e in self.evaluations if e.latest), None)

    def __repr__(self) -> str:
        """Return a string representation."""
        return f"<Issue(source={self.source!r}, external_id={self.external_id!r}, title={self.title!r})>"
